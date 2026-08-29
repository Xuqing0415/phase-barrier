"""K8s sidecar HTTP 门禁服务（v0.7.0）。

在 Kubernetes 中以 sidecar 容器运行：与编码 Agent 共享工作区卷，
独占挂载门禁目录卷（``.agent_gate``）。Agent 容器完全不挂载门禁目录，
只能通过本服务的 HTTP API 查询阶段、推进阶段、上报测试运行与源码变更，
从而在“Agent 绕过工具包装直接操作文件系统”的场景下依然无法篡改状态。

端点：
- ``GET  /healthz``                  存活探针
- ``GET  /api/state``                当前阶段 JSON
- ``POST /api/advance``              ``{"new_stage": N}`` 推进阶段（走完整证据校验）
- ``POST /api/test-run``             ``{"exit_code": 0, "output": "..."}`` 上报测试运行结果
- ``POST /api/source-change``        ``{"path": "fib.py"}`` 上报源码/测试变更

v0.9.0：新增 ``--audit-remote-url`` / ``--audit-remote-token``，把审计事件异步
转发到 SIEM / webhook；关闭时自动冲刷远程审计队列。

部署模板见 ``deploy/k8s/``；本地试运行：:

    python -m anti_shortcut.sidecar --workspace . --port 8080
"""
from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import load_config
from .interceptors import summarize_test_output
from .skill import AntiShortcutSkill


class GateSidecar:
    """持有 Skill 实例，并以线程安全方式对外提供门禁操作。"""

    def __init__(
        self,
        workspace: str | Path,
        config: str | Path | dict[str, Any] | None = None,
        user_request: str = "",
    ) -> None:
        self.skill = AntiShortcutSkill(workspace, config=config, user_request=user_request)
        self._lock = threading.Lock()

    def state(self) -> dict[str, Any]:
        return {
            "current_stage": self.skill.current_stage,
            "stage_name": self.skill.stage_name,
            "is_complete": self.skill.is_complete,
            "completed_stages": list(self.skill.state.completed_stages),
        }

    def advance(self, new_stage: int) -> dict[str, Any]:
        with self._lock:
            return self.skill.advance_stage(new_stage)

    def record_test_run(self, exit_code: int, output: str) -> dict[str, Any]:
        record = summarize_test_output(output or "", exit_code, adapter=self.skill.adapter)
        with self._lock:
            self.skill.state.mark_test_run(record)
        visible = {k: v for k, v in record.items() if k != "output_tail"}
        return {"ok": True, "record": visible}

    def record_source_change(self, path: str) -> dict[str, Any]:
        with self._lock:
            self.skill.state.mark_source_change(path)
        return {"ok": True, "path": path}


def make_handler(sidecar: GateSidecar) -> type[BaseHTTPRequestHandler]:
    """为指定 sidecar 构造 HTTP 处理器（关闭默认访问日志）。"""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            pass

        def _send(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return {}
            return data if isinstance(data, dict) else {}

        def do_GET(self) -> None:  # noqa: N802（HTTP 动词方法名）
            if self.path == "/healthz":
                self._send(200, {"status": "ok"})
            elif self.path == "/api/state":
                self._send(200, sidecar.state())
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path == "/api/advance":
                data = self._read_json()
                new_stage = data.get("new_stage")
                if not isinstance(new_stage, int):
                    self._send(400, {"error": "new_stage 必须是整数"})
                    return
                result = sidecar.advance(new_stage)
                self._send(200 if result.get("success") else 409, result)
            elif self.path == "/api/test-run":
                data = self._read_json()
                exit_code = data.get("exit_code")
                if not isinstance(exit_code, int):
                    self._send(400, {"error": "exit_code 必须是整数"})
                    return
                self._send(200, sidecar.record_test_run(exit_code, data.get("output", "")))
            elif self.path == "/api/source-change":
                data = self._read_json()
                path = data.get("path")
                if not isinstance(path, str) or not path:
                    self._send(400, {"error": "path 必须是字符串"})
                    return
                self._send(200, sidecar.record_source_change(path))
            else:
                self._send(404, {"error": "not found"})

    return Handler


def _merge_config(args: argparse.Namespace) -> Any:
    """合并配置文件 / 命令行 / 环境变量中的远程审计参数。

    优先级：命令行 > 配置文件 > 环境变量 ``AUDIT_REMOTE_URL`` / ``AUDIT_REMOTE_TOKEN``
    （K8s 场景用 Secret 注入环境变量即可，无需改 Deployment args）。
    """
    cfg = None
    if args.config:
        cfg = load_config(args.config)
    url = args.audit_remote_url or os.environ.get("AUDIT_REMOTE_URL") or ""
    token = args.audit_remote_token or os.environ.get("AUDIT_REMOTE_TOKEN") or ""
    if url or token:
        if cfg is None:
            cfg = {"audit_remote_url": url or None, "audit_remote_token": token or None}
        else:
            if url:
                cfg.audit_remote_url = url
            if token:
                cfg.audit_remote_token = token
    return cfg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="phase-barrier K8s sidecar 门禁服务")
    parser.add_argument("--workspace", default=".", help="工作区路径（与 Agent 共享的卷）")
    parser.add_argument("--config", default=None, help="phase-barrier YAML 配置路径（可选）")
    parser.add_argument("--user-request", default="", help="用户需求原文（阶段 0 证据）")
    parser.add_argument(
        "--state-key",
        default="",
        help="状态签名 HMAC 密钥（等价于环境变量 PHASE_BARRIER_HMAC_KEY；生产环境建议用 Secret 注入）",
    )
    parser.add_argument(
        "--audit-remote-url",
        default="",
        help="审计远程推送端点（SIEM / webhook，v0.9.0；也可用环境变量 AUDIT_REMOTE_URL）",
    )
    parser.add_argument(
        "--audit-remote-token",
        default="",
        help="审计远程推送 Bearer Token（可选；生产环境建议用 Secret 注入）",
    )
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8080, help="监听端口")
    args = parser.parse_args(argv)
    if args.state_key:
        os.environ["PHASE_BARRIER_HMAC_KEY"] = args.state_key

    sidecar = GateSidecar(
        Path(args.workspace),
        config=_merge_config(args),
        user_request=args.user_request,
    )
    server = ThreadingHTTPServer((args.host, args.port), make_handler(sidecar))
    print(
        f"[sidecar] phase-barrier 门禁服务已启动: http://{args.host}:{args.port}"
        f"（工作区 {args.workspace}，阶段 {sidecar.skill.current_stage}）",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        sidecar.skill.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())