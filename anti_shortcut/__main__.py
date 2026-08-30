"""命令行入口：外部门禁检查与阶段推进（供编排器 / 人工监督使用）。

用法::

    python -m anti_shortcut inspect [--workspace .] [--json]
    python -m anti_shortcut advance --to 2 [--workspace .] [--json]
    python -m anti_shortcut verify-evidence [--workspace .] [--json]
    python -m anti_shortcut export-evidence [--workspace .] [--out evidence-bundle.json]
    python -m anti_shortcut rotate-key --to <new-key> [--from <old-key>] [--workspace .]
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
from .config import STAGES, load_config
from .evidence import (
    EVIDENCE_MANIFEST_NAME,
    EVIDENCE_MANIFEST_VERSION,
    EvidenceManifest,
    EvidenceManifestError,
)
from .paths import sha256_file
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

    p_inspect = sub.add_parser("inspect", parents=[common], help="查看当前门禁状态")
    p_inspect.set_defaults(func=_cmd_inspect)

    p_advance = sub.add_parser("advance", parents=[common], help="推进阶段（校验当前阶段证据）")
    p_advance.add_argument("--to", type=int, required=True, help="目标阶段（必须等于当前阶段 + 1）")
    p_advance.add_argument("--user-request", type=str, default="", help="用户需求原文（首次初始化时记录）")
    p_advance.set_defaults(func=_cmd_advance)

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

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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