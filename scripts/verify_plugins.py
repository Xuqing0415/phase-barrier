"""plugins.json 插件索引自动验证脚本（v0.45.0）。

与包内 ``anti_shortcut.plugins.verify_plugins``（当前环境已安装插件的入口点冒烟
验证）分工：本脚本负责“索引层”验证 —— 读取仓库根目录 ``plugins.json``，逐个安装
索引条目（给出 ``install`` 的本地路径 / 可安装 URL），在**全新子进程**中运行
``python -m anti_shortcut plugin-verify --json``（安装后需新进程才能看到新注册的
入口点），断言条目声明的入口点全部可用，并按 ``--update`` 把最新 ``status`` /
``last_verified`` 写回 ``plugins.json``。

用法::

    python scripts/verify_plugins.py                  # 验证并打印摘要
    python scripts/verify_plugins.py --update         # 验证并把状态写回 plugins.json
    python scripts/verify_plugins.py --json           # stdout 输出结构化报告
    python scripts/verify_plugins.py --no-install     # 跳过安装（插件须已安装）

退出码：0 = 全部通过；1 = 存在失败（``--update`` 时仍会把失败状态写回，供
``plugin-verification.yml`` 周期工作流提交 / 人工处置）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = REPO_ROOT / "plugins.json"

# 声明入口点简写 -> 完整入口点组
GROUP_ALIASES: dict[str, str] = {
    "languages": "phase_barrier.languages",
    "validators": "phase_barrier.validators",
    "interceptors": "phase_barrier.interceptors",
    "integrations": "anti_shortcut.integrations",
}


def now_iso() -> str:
    """当前 UTC 时间的 ISO-8601 字符串（秒级，Z 后缀）。"""
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def load_index(path: Path) -> list[dict]:
    """读取 plugins.json，返回条目列表（顶层必须是数组）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"plugins.json 顶层必须是数组，得到 {type(data).__name__}")
    return data


def _resolve_target(entry: dict) -> str | None:
    """把条目 ``install`` / ``repo`` 解析为 pip 可安装目标（本地路径 -> 绝对路径）。"""
    raw = (entry.get("install") or entry.get("repo") or "").strip()
    if not raw:
        return None
    if raw.startswith(("./", "../", ".\\")):
        return str((REPO_ROOT / raw).resolve())
    if raw.startswith(("http://", "https://", "git+")):
        return raw
    return str((REPO_ROOT / raw).resolve())


def install_entry(entry: dict) -> str | None:
    """安装单个索引条目；返回错误信息，成功返回 None。"""
    target = _resolve_target(entry)
    if target is None:
        return "缺少 install / repo 字段，无法安装"
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-e", target],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = "；".join(tail[-3:]) if tail else f"退出码 {proc.returncode}"
        return f"pip install 失败: {detail}"
    return None


def run_plugin_verify() -> dict:
    """在独立子进程中运行 plugin-verify --json，返回 {ok, plugins, ...}。

    pip editable 安装通过 site-packages 下的 .pth 注册路径，只在**解释器启动**时
    生效，因此必须在安装完成后另起进程验证（与 plugin-check.yml 同款做法）。
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "anti_shortcut", "plugin-verify", "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"plugin-verify 子进程失败（rc={proc.returncode}）: "
            f"{(proc.stderr or proc.stdout or '').strip()[-500:]}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"plugin-verify JSON 解析失败: {exc}") from exc


def _group_and_names(entry: dict) -> list[tuple[str, list[str]]]:
    """展开条目声明的入口点：{组: [名称]} 或 [组, ...] 简写均可。"""
    eps = entry.get("entry_points") or {}
    out: list[tuple[str, list[str]]] = []
    if isinstance(eps, dict):
        for group, names in eps.items():
            group = GROUP_ALIASES.get(group, group)
            if isinstance(names, str):
                names = [names]
            out.append((group, list(names or [])))
    else:
        for group in eps:  # 兼容 ["phase_barrier.languages", ...] 简写
            group = GROUP_ALIASES.get(group, group)
            out.append((group, []))
    return out


def check_entry(entry: dict, plugin_results: dict) -> list[str]:
    """对照 plugin-verify 的 plugins 结果检查条目声明的入口点，返回错误列表。"""
    failures: list[str] = []
    for group, names in _group_and_names(entry):
        group_results = plugin_results.get(group, {})
        if not names:
            ok_names = [n for n, info in group_results.items() if info.get("ok")]
            if not ok_names:
                failures.append(f"{group} 组内没有可用的入口点（插件未安装？）")
            continue
        for name in names:
            info = group_results.get(name)
            if info is None:
                failures.append(f"{group}:{name} 未注册（插件未安装或入口点缺失）")
            elif not info.get("ok"):
                errors = "；".join(info.get("errors") or [])
                failures.append(f"{group}:{name} -> {errors}")
    return failures


def verify_index(index: list[dict], *, install: bool) -> dict:
    """验证整个索引，返回结构化报告。"""
    install_errors: dict[str, str] = {}
    if install:
        for entry in index:
            err = install_entry(entry)
            if err:
                install_errors[entry["name"]] = err
    plugin_results = run_plugin_verify().get("plugins", {})
    plugins: dict[str, dict] = {}
    for entry in index:
        name = entry.get("name") or "<unnamed>"
        declared = [
            {"group": group, "entries": names}
            for group, names in _group_and_names(entry)
        ]
        if not declared:
            # 无入口点声明的占位条目：不自动判定，保留人工状态
            plugins[name] = {
                "status": "unverified",
                "entry_points": [],
                "detail": "未声明入口点，跳过自动验证",
            }
            continue
        failures = [install_errors[name]] if name in install_errors else []
        failures.extend(check_entry(entry, plugin_results))
        status = "passed" if not failures else "failed"
        plugins[name] = {
            "status": status,
            "entry_points": declared,
            "detail": "全部声明入口点验证通过" if not failures else "；".join(failures),
        }
    return {
        "verified_at": now_iso(),
        "ok": all(p["status"] != "failed" for p in plugins.values()),
        "plugins": plugins,
    }


def update_index(path: Path, index: list[dict], report: dict) -> None:
    """把报告中的状态写回 plugins.json（仅覆盖自动验证过的条目）。"""
    by_name = report["plugins"]
    verified_at = report["verified_at"]
    for entry in index:
        info = by_name.get(entry.get("name"))
        if not info or info["status"] == "unverified":
            continue
        entry["status"] = info["status"]
        entry["last_verified"] = verified_at
    payload = json.dumps(index, ensure_ascii=False, indent=2) + "\n"
    Path(path).write_text(payload, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="plugins.json 插件索引自动验证（v0.45.0）")
    parser.add_argument("--index", default=str(DEFAULT_INDEX), help="索引文件路径（默认 plugins.json）")
    parser.add_argument("--update", action="store_true", help="验证后把 status / last_verified 写回索引")
    parser.add_argument("--json", action="store_true", help="stdout 输出结构化报告")
    parser.add_argument("--no-install", action="store_true", help="跳过条目安装（插件须已安装）")
    args = parser.parse_args(argv)

    index_path = Path(args.index)
    index = load_index(index_path)
    report = verify_index(index, install=not args.no_install)
    if args.update:
        update_index(index_path, index, report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        failed = [n for n, p in report["plugins"].items() if p["status"] == "failed"]
        summary = f"plugins.json 验证完成（{report['verified_at']}）"
        if failed:
            summary += f"：{len(failed)} 个失败 -> {', '.join(failed)}"
        else:
            summary += f"：全部 {len(report['plugins'])} 个索引条目通过"
        print(summary)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
