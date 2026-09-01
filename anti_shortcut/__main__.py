"""命令行入口：外部门禁检查与阶段推进（供编排器 / 人工监督使用）。

用法::

    python -m anti_shortcut inspect [--workspace .] [--json]
    python -m anti_shortcut advance --to 2 [--workspace .] [--json]
    python -m anti_shortcut verify-evidence [--workspace .] [--json]
    python -m anti_shortcut export-evidence [--workspace .] [--out evidence-bundle.json]
    python -m anti_shortcut rotate-key --to <new-key> [--from <old-key>] [--workspace .]
    python -m anti_shortcut init [--language auto] [--output config.yaml] [--force]
    python -m anti_shortcut --version

``advance`` 与 Agent 内部的 ``advance_stage`` 走同一套证据校验：
通过返回退出码 0，被拒绝返回退出码 1 并打印原因。

v0.9.0 新增：
- ``verify-evidence``：对照工作区校验证据签名清单（检测证据文件事后篡改）。
- ``export-evidence``：把证据清单 + 文件哈希导出为可审计 bundle（v0.10.0）。
- ``rotate-key``：轮换状态签名 HMAC 密钥（支持从无签名状态启用签名）。

v0.11.0 新增：
- ``verify-evidence --git-base <ref>``：Git 门禁——列出当前分支相对基线改动的文件，
  若与证据清单条目有交集则判定“证据文件被事后篡改”并失败（供 CI / GitHub Action 使用）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from . import __version__
from .config import STAGES, GateConfig, load_config
from .init import init_config
from .evidence import (
    EVIDENCE_MANIFEST_NAME,
    EVIDENCE_MANIFEST_VERSION,
    EvidenceManifest,
    EvidenceManifestError,
)
from .paths import sha256_file
from .languages import get_adapter
from .proxy import ExecDenied, GateProxy, ProxyError, WriteDenied
from .sdk import PhaseBarrier, classify_stage_path
from .skill import AntiShortcutSkill
from .state import CorruptedStateError, StateManager


def _build_skill(args: argparse.Namespace) -> AntiShortcutSkill:
    ws = Path(args.workspace).resolve()
    if not ws.is_dir():
        raise FileNotFoundError(f"工作区不存在或不是目录: {ws}")
    return AntiShortcutSkill(
        ws,
        config=args.config,
        user_request=getattr(args, "user_request", "") or "",
    )


def _cmd_init(args: argparse.Namespace) -> int:
    """生成 phase-barrier 配置模板（v0.26.0）。"""
    ws = Path(args.workspace).resolve()
    if not ws.is_dir():
        raise FileNotFoundError(f"工作区不存在或不是目录: {ws}")
    rules = None
    if args.rules:
        rules = [r.strip() for r in args.rules.split(",") if r.strip()]
    coverage = None
    if args.with_coverage:
        coverage = args.coverage_threshold
    out, _text = init_config(
        ws,
        language=args.language,
        output=args.output,
        force=args.force,
        coverage_threshold=coverage,
        hmac_key=args.hmac_key,
        audit_url=args.audit_url,
        rules=rules,
    )
    if args.json:
        print(
            json.dumps(
                {"ok": True, "output": str(out), "language": args.language or "auto"},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"OK: 已生成配置 {out}")
        print(f"    语言: {args.language or '自动检测'}")
        print("    下一步：python -m anti_shortcut inspect --workspace . --config " + str(out))
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    skill = _build_skill(args)
    tr = skill.state.get_evidence("last_test_run") or {}
    payload = {
        "workspace": str(skill.workspace),
        "current_stage": skill.current_stage,
        "stage_name": skill.stage_name,
        "completed_stages": skill.state.completed_stages,
        "complete": skill.is_complete,
        "last_test_run": (
            {k: tr.get(k) for k in ("exit_code", "passed", "summary")} if tr else None
        ),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"workspace       : {payload['workspace']}")
        print(f"current_stage   : {payload['current_stage']}（{payload['stage_name']}）")
        print(f"completed_stages: {payload['completed_stages']}")
        print(f"complete        : {payload['complete']}")
        if payload["last_test_run"]:
            ltr = payload["last_test_run"]
            print(
                f"last_test_run   : exit_code={ltr['exit_code']} "
                f"passed={ltr['passed']} summary={ltr['summary']!r}"
            )
    return 0


def _cmd_advance(args: argparse.Namespace) -> int:
    skill = _build_skill(args)
    result = skill.advance_stage(args.to)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["success"]:
        print(f"OK: {result['message']}")
    else:
        print(f"REJECTED: {result['error']}")
    return 0 if result["success"] else 1



def _cmd_check(args: argparse.Namespace) -> int:
    """编排器钩子校验：检查是否放行进入指定阶段（只读，v0.22.0）。

    与 SDK ``PhaseBarrier.check(stage)`` 等价：Agent 声称要进入 / 处于某阶段，
    校验其前置证据是否满足。退出码：0 = 放行；1 = 拒绝（证据不足 / 参数非法）。
    """
    ws = Path(args.workspace).resolve()
    if not ws.is_dir():
        raise FileNotFoundError(f"工作区不存在或不是目录: {ws}")
    barrier = PhaseBarrier(workspace=ws, config=args.config)
    try:
        result = barrier.check(args.stage)
    finally:
        barrier.close()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["allowed"]:
        print(f"OK: {result['message']}")
    else:
        print(f"DENIED: {result['message']}", file=sys.stderr)
        for v in result["violations"]:
            print(f"  - {v}", file=sys.stderr)
    return 0 if result["allowed"] else 1

def _git_changed_files(ws: Path, git_base: str) -> list[str]:
    """返回当前分支相对 git_base 改动的文件（``git diff --name-only <base>...HEAD``）。"""
    proc = subprocess.run(
        ["git", "-C", str(ws), "diff", "--name-only", f"{git_base}...HEAD"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:300]
        raise ValueError(
            f"git 门禁检查失败（git diff --name-only {git_base}...HEAD）: "
            f"{err or 'git 命令返回非零'}；请确认工作区是 git 仓库且基线 ref 存在"
            "（CI 中可用 fetch-depth: 0 拉全量历史）"
        )
    return [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]


def _git_change_impact(
    ws: Path, changed: list[str], config: GateConfig
) -> list[dict]:
    """把 git 变更文件映射到受影响的门禁阶段（PR 增量校验提示，v0.26.0）。

    v0.26.2：分类逻辑复用 ``sdk.classify_stage_path``（与编排器 SDK
    ``PhaseBarrier.stage_of()`` 保持一致），输出字段不变。
    """
    adapter = get_adapter(config, ws)
    impact: list[dict] = []
    for rel in sorted(changed):
        info = classify_stage_path(adapter, config, rel)
        impact.append({"file": rel, "kind": info["kind"], "requires": info["requires"]})
    return impact


def _cmd_verify_evidence(args: argparse.Namespace) -> int:
    """校验证据签名清单：对照工作区当前文件，检测缺失 / 被篡改的证据。

    v0.11.0：``--git-base <ref>`` 开启 Git 门禁——本次变更改动任何证据文件即失败。
    """
    ws = Path(args.workspace).resolve()
    if not ws.is_dir():
        raise FileNotFoundError(f"工作区不存在或不是目录: {ws}")
    cfg = load_config(args.config)
    manifest = EvidenceManifest(
        ws / cfg.gate_dir_name / EVIDENCE_MANIFEST_NAME,
        hmac_key=cfg.state_hmac_key or os.environ.get("PHASE_BARRIER_HMAC_KEY"),
    )
    ok, violations = manifest.verify(ws)
    git_base = getattr(args, "git_base", None)
    changed: list[str] = []
    if git_base:
        try:
            changed = _git_changed_files(ws, git_base)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        entries = set(manifest.entries())
        touched = sorted({c for c in changed if c in entries})
        if touched:
            ok = False
            violations.extend(f"证据文件在本次变更中被修改: {c}" for c in touched)
    git_impact: list[dict] = []
    if git_base and changed:
        git_impact = _git_change_impact(ws, changed, cfg)
    if args.json:
        print(
            json.dumps(
                {
                    "ok": ok,
                    "violations": violations,
                    "entries": sorted(manifest.entries()),
                    "signed": manifest.is_signed(),
                    "git_base": git_base,
                    "git_changed_files": changed,
                    "git_impact": git_impact,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        entries = sorted(manifest.entries())
        signed = "已签名" if manifest.is_signed() else "未签名（未配置 HMAC 密钥）"
        if ok:
            print(
                f"OK: 证据清单校验通过（{len(entries)} 个证据文件，{signed}）："
                + ", ".join(entries)
            )
        else:
            print(f"FAIL: 证据清单校验未通过（{len(entries)} 个证据文件，{signed}）")
            for v in violations:
                print(f"VIOLATION: {v}", file=sys.stderr)
        if git_base and changed:
            print(f"git 门禁: 基线 {git_base}，本次变更 {len(changed)} 个文件")
            for item in git_impact:
                print(f"  - {item['file']} [{item['kind']}] {item['requires']}")
    return 0 if ok else 1


def _cmd_export_evidence(args: argparse.Namespace) -> int:
    """导出证据清单为可审计 bundle（清单 + 当前文件哈希 + 校验结果）。"""
    ws = Path(args.workspace).resolve()
    if not ws.is_dir():
        raise FileNotFoundError(f"工作区不存在或不是目录: {ws}")
    cfg = load_config(args.config)
    manifest = EvidenceManifest(
        ws / cfg.gate_dir_name / EVIDENCE_MANIFEST_NAME,
        hmac_key=cfg.state_hmac_key or os.environ.get("PHASE_BARRIER_HMAC_KEY"),
    )
    ok, violations = manifest.verify(ws)
    entries = manifest.entries()
    files: dict[str, dict] = {}
    for rel in sorted(entries):
        target = ws / rel
        if target.is_file():
            try:
                files[rel] = {
                    "sha256": sha256_file(target),
                    "size": target.stat().st_size,
                    "stage": entries[rel]["stage"],
                }
            except OSError:
                files[rel] = {"error": "不可读", "stage": entries[rel]["stage"]}
    from datetime import datetime, timezone

    bundle = {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "manifest_version": EVIDENCE_MANIFEST_VERSION,
        "signed": manifest.is_signed(),
        "verified": ok,
        "violations": violations,
        "entries": entries,
        "files": files,
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
        status = "校验通过" if ok else "存在违规"
        print(f"OK: 证据清单已导出到 {out}（{status}）")
    else:
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _cmd_rotate_key(args: argparse.Namespace) -> int:
    """轮换状态签名 HMAC 密钥：校验现有签名后以新密钥重新签名。"""
    ws = Path(args.workspace).resolve()
    if not ws.is_dir():
        raise FileNotFoundError(f"工作区不存在或不是目录: {ws}")
    cfg = load_config(args.config)
    state_file = ws / cfg.gate_dir_name / cfg.state_file_name
    old_key = args.from_key or cfg.state_hmac_key or os.environ.get("PHASE_BARRIER_HMAC_KEY")
    manager = StateManager(
        state_file,
        hmac_key=old_key or None,
        hmac_keys=cfg.state_hmac_keys,
    )
    manager.rotate_key(args.to_key, keep_old=args.keep_old)
    suffix = "（旧密钥保留为轮换期验证密钥）" if args.keep_old else ""
    print(f"OK: 状态签名密钥已轮换{suffix}")
    return 0


def _cmd_write(args: argparse.Namespace) -> int:
    """经门禁写入工作区文件（v0.18.0，透明代理的 CLI 形态）。

    退出码：0 = 写入成功；2 = 被阶段门禁拒绝；1 = 参数或环境错误。
    """
    ws = Path(args.workspace).resolve()
    if not ws.is_dir():
        raise FileNotFoundError(f"工作区不存在或不是目录: {ws}")
    if args.content is not None and args.stdin:
        print("ERROR: --content 与 --stdin 不能同时使用", file=sys.stderr)
        return 1
    if args.stdin:
        content = sys.stdin.read()
    elif args.content is not None:
        content = args.content
    else:
        print("ERROR: 必须提供 --content 或 --stdin", file=sys.stderr)
        return 1
    skill = AntiShortcutSkill(ws, config=args.config)
    try:
        result = GateProxy(skill).write_file(args.path, content)
    except WriteDenied as exc:
        payload = {"ok": False, "error": exc.reason, "path": args.path}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"DENIED: {exc.reason}")
        return 2
    except ProxyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        skill.close()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"OK: 已写入 {result['path']}（{result['kind']}）")
    return 0


def _cmd_exec(args: argparse.Namespace) -> int:
    """经门禁执行 shell 命令（v0.18.0，透明代理的 CLI 形态）。

    退出码语义：0 = 放行且命令退出码为 0；命令自身退出码（1-255）= 放行但失败；
    2 = 被阶段门禁拒绝；1 = 参数或环境错误。
    """
    ws = Path(args.workspace).resolve()
    if not ws.is_dir():
        raise FileNotFoundError(f"工作区不存在或不是目录: {ws}")
    skill = AntiShortcutSkill(ws, config=args.config)
    try:
        result = GateProxy(skill).execute_command(
            args.command, cwd=args.cwd, timeout=args.timeout
        )
    except ExecDenied as exc:
        payload = {"ok": False, "error": exc.reason, "command": args.command}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"DENIED: {exc.reason}")
        return 2
    except ProxyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        skill.close()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"exit_code={result['exit_code']} "
            f"recorded_test_run={result.get('recorded_test_run', False)}"
        )
        if result.get("output"):
            output = result["output"]
            sys.stdout.write(output if output.endswith("\n") else output + "\n")
    if result["exit_code"] == 0:
        return 0
    return result["exit_code"] if result["exit_code"] > 0 else 1


def _cmd_sidecar(args: argparse.Namespace) -> int:
    """运行 sidecar 门禁 HTTP 服务（阻塞，Ctrl+C 退出；v0.20.0）。

    与 ``python -m anti_shortcut.sidecar`` 等价，提供统一的 CLI 入口。
    远程审计参数（--audit-remote-*）与 HMAC 密钥可通过环境变量或 YAML 配置注入。
    """
    from . import sidecar as _sidecar_module

    argv = ["--workspace", args.workspace, "--host", args.host, "--port", str(args.port)]
    if args.config:
        argv += ["--config", args.config]
    if args.user_request:
        argv += ["--user-request", args.user_request]
    if args.state_key:
        argv += ["--state-key", args.state_key]
    if args.tls_cert:
        argv += ["--tls-cert", args.tls_cert]
    if args.tls_key:
        argv += ["--tls-key", args.tls_key]
    if args.tls_client_ca:
        argv += ["--tls-client-ca", args.tls_client_ca]
    return _sidecar_module.main(argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m anti_shortcut",
        description="反捷径校验 Skill 命令行：状态检查与阶段推进",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=str, default=".", help="工作区根目录（默认当前目录）")
    common.add_argument("--config", type=str, default=None, help="YAML 配置文件路径")
    common.add_argument("--json", action="store_true", help="以 JSON 输出")

    p_init = sub.add_parser(
        "init", parents=[common], help="生成 phase-barrier 配置模板（v0.26.0）"
    )
    p_init.add_argument("--language", type=str, default="", help="指定语言（缺省自动检测，如 python / javascript / cpp）")
    p_init.add_argument("--output", type=str, default="config.yaml", help="输出文件路径（默认 config.yaml）")
    p_init.add_argument("--force", action="store_true", help="覆盖已存在的配置文件")
    p_init.add_argument("--with-coverage", action="store_true", help="启用覆盖率门禁")
    p_init.add_argument("--coverage-threshold", type=float, default=80.0, help="覆盖率阈值百分比（默认 80）")
    p_init.add_argument("--hmac-key", type=str, default="", help="状态签名 HMAC 密钥（推荐生产启用）")
    p_init.add_argument("--audit-url", type=str, default="", help="审计远程推送端点（SIEM / webhook）")
    p_init.add_argument("--rules", type=str, default="", help="内置安全规则，逗号分隔（如 no_path_traversal,no_shell_injection）")
    p_init.set_defaults(func=_cmd_init)

    p_inspect = sub.add_parser("inspect", parents=[common], help="查看当前门禁状态")
    p_inspect.set_defaults(func=_cmd_inspect)

    p_advance = sub.add_parser("advance", parents=[common], help="推进阶段（校验当前阶段证据）")
    p_advance.add_argument("--to", type=int, required=True, help="目标阶段（必须等于当前阶段 + 1）")
    p_advance.add_argument("--user-request", type=str, default="", help="用户需求原文（首次初始化时记录）")
    p_advance.set_defaults(func=_cmd_advance)
    p_check = sub.add_parser(
        "check", parents=[common], help="检查是否放行进入指定阶段（只读，v0.22.0）"
    )
    p_check.add_argument("--stage", type=int, required=True, help="Agent 声称的阶段号（0-6）")
    p_check.set_defaults(func=_cmd_check)

    p_verify = sub.add_parser(
        "verify-evidence", parents=[common], help="对照工作区校验证据签名清单"
    )
    p_verify.add_argument(
        "--git-base",
        type=str,
        default=None,
        help="Git 基线 ref（如 origin/main）：检测证据文件是否在本次变更中被修改（CI 门禁，v0.11.0）",
    )
    p_verify.set_defaults(func=_cmd_verify_evidence)

    p_export = sub.add_parser(
        "export-evidence", parents=[common], help="把证据清单导出为可审计 bundle"
    )
    p_export.add_argument(
        "--out", type=str, default="", help="导出文件路径（缺省输出到 stdout）"
    )
    p_export.set_defaults(func=_cmd_export_evidence)

    p_rotate = sub.add_parser(
        "rotate-key", parents=[common], help="轮换状态签名 HMAC 密钥"
    )
    p_rotate.add_argument("--to", dest="to_key", required=True, help="新签名密钥")
    p_rotate.add_argument(
        "--from",
        dest="from_key",
        default="",
        help="旧签名密钥（缺省时使用配置 state_hmac_key / 环境变量 PHASE_BARRIER_HMAC_KEY）",
    )
    p_rotate.add_argument(
        "--keep-old", action="store_true", help="把旧密钥保留为轮换期验证密钥（宽限期双密钥）"
    )
    p_rotate.set_defaults(func=_cmd_rotate_key)

    p_write = sub.add_parser(
        "write", parents=[common], help="经门禁写入工作区文件（v0.18.0）"
    )
    p_write.add_argument("--path", type=str, required=True, help="目标路径（须解析在工作区内）")
    p_write.add_argument("--content", type=str, default=None, help="文件内容（与 --stdin 二选一）")
    p_write.add_argument("--stdin", action="store_true", help="从 stdin 读取文件内容")
    p_write.set_defaults(func=_cmd_write)

    p_exec = sub.add_parser(
        "exec", parents=[common], help="经门禁执行 shell 命令（v0.18.0）"
    )
    p_exec.add_argument("--command", type=str, required=True, help="要执行的 shell 命令")
    p_exec.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="超时秒数（1-3600，默认 120）",
    )
    p_exec.add_argument(
        "--cwd",
        type=str,
        default=None,
        help="命令工作目录（相对/绝对路径，须在工作区内；默认工作区根）",
    )
    p_exec.set_defaults(func=_cmd_exec)

    p_sidecar = sub.add_parser(
        "sidecar", help="运行 sidecar 门禁 HTTP 服务（阻塞，Ctrl+C 退出；v0.20.0）"
    )
    p_sidecar.add_argument("--workspace", type=str, default=".", help="工作区路径（默认当前目录）")
    p_sidecar.add_argument("--config", type=str, default=None, help="YAML 配置文件路径（可选）")
    p_sidecar.add_argument(
        "--user-request", type=str, default="", help="用户需求原文（阶段 0 证据，可选）"
    )
    p_sidecar.add_argument(
        "--state-key",
        type=str,
        default="",
        help="状态签名 HMAC 密钥（也可用环境变量 PHASE_BARRIER_HMAC_KEY）",
    )
    p_sidecar.add_argument("--host", type=str, default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    p_sidecar.add_argument("--port", type=int, default=8080, help="监听端口（默认 8080）")
    p_sidecar.add_argument(
        "--tls-cert",
        type=str,
        default="",
        help="mTLS 服务端证书 PEM（与 --tls-key / --tls-client-ca 同时启用，v0.21.0）",
    )
    p_sidecar.add_argument("--tls-key", type=str, default="", help="mTLS 服务端私钥 PEM")
    p_sidecar.add_argument(
        "--tls-client-ca", type=str, default="", help="客户端证书签发 CA（PEM），启用后强制客户端证书"
    )
    p_sidecar.set_defaults(func=_cmd_sidecar)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CorruptedStateError as exc:
        print(f"ERROR: 门禁状态不可用: {exc}", file=sys.stderr)
        return 1
    except EvidenceManifestError as exc:
        print(f"ERROR: 证据清单不可用: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except (ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: 配置无效: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR: 无法访问工作区或门禁目录: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())