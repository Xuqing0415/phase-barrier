"""命令行入口：外部门禁检查与阶段推进（供编排器 / 人工监督使用）。

用法::

    python -m anti_shortcut inspect [--workspace .] [--json]
    python -m anti_shortcut advance --to 2 [--workspace .] [--json]
    python -m anti_shortcut --version

``advance`` 与 Agent 内部的 ``advance_stage`` 走同一套证据校验：
通过返回退出码 0，被拒绝返回退出码 1 并打印原因。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from . import __version__
from .config import STAGES
from .skill import AntiShortcutSkill
from .state import CorruptedStateError


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
