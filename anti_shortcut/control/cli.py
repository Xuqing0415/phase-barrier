"""CLI for authenticated control-plane operations and local initial enrollment."""
from __future__ import annotations

import json
import os


def run(args):
    if args.command == "tui":
        from .tui import GateApp
        from ..proxy_client import GateClient
        GateApp(GateClient(args.url, token=os.environ.get(args.token_env))).run()
        return 0
    from ..skill import AntiShortcutSkill
    skill = AntiShortcutSkill(args.workspace, config=args.config,
                              session_token=os.environ.get(args.token_env))
    try:
        control = skill.control
        if args.command == "permissions-init":
            result = {"token": control.permissions.bootstrap(), "role": "admin", "actor": "human"}
        elif args.command == "control":
            result = control.snapshot() if args.operation == "snapshot" else control.dispatch(
                args.operation, json.loads(args.data))
        elif args.command == "task":
            if args.action == "list":
                result = control.tasks()
            elif args.action == "capture":
                result = control.task_capture(args.title, args.type, args.goal_id)
            else:
                status = {"queue": "queued", "claim": "active", "complete": "done", "drop": "dropped"}
                result = control.task_transition(args.task_id, status[args.action], args.return_to_main)
        elif args.command == "goal":
            if args.action == "status":
                result = control.goals()
            elif args.action == "create":
                result = control.goal_create(args.title, args.done, args.milestone)
            else:
                result = control.goal_action(args.goal_id, args.action)
        else:
            raise ValueError("unknown control command")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        skill.close()


def add_parsers(sub, common):
    for command in ["permissions-init", "control", "task", "goal", "tui"]:
        parser = sub.add_parser(command, parents=[common], help="阶段门禁控制面 " + command)
        parser.add_argument("--token-env", default="PHASE_BARRIER_SESSION_TOKEN",
                            help="保存会话令牌的环境变量名（避免令牌出现在命令行）")
        parser.set_defaults(func=run)
        if command == "control":
            parser.add_argument("operation", help="snapshot 或受控操作名称")
            parser.add_argument("--data", default="{}", help="操作参数 JSON 对象")
        elif command == "task":
            parser.add_argument("action", choices=["list", "capture", "queue", "claim", "complete", "drop"])
            parser.add_argument("--task-id", default="")
            parser.add_argument("--title", default="")
            parser.add_argument("--type", choices=["main", "btw", "blocker"], default="btw")
            parser.add_argument("--goal-id", default=None)
            parser.add_argument("--return-to-main", action="store_true")
        elif command == "goal":
            parser.add_argument("action", choices=["status", "create", "pause", "resume", "next", "abandon"])
            parser.add_argument("--goal-id", default="")
            parser.add_argument("--title", default="")
            parser.add_argument("--done", action="append", default=[])
            parser.add_argument("--milestone", action="append", default=[])
        elif command == "tui":
            parser.add_argument("--url", default="http://127.0.0.1:8080")
