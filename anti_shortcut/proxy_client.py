"""Agent 侧透明代理客户端（v0.17.0）。

供编码 Agent 框架把 ``write_file`` / ``execute_command`` 重定向到 sidecar 的
``/api/write`` / ``/api/exec``，从而在“Agent 绕过自身工具包装”时依然受门禁约束。

用法::

    from anti_shortcut.proxy_client import GateClient, GateDenied

    gate = GateClient("http://127.0.0.1:8080")
    try:
        gate.write_file("fib.py", "def fib(n): ...")
    except GateDenied as exc:
        print("被拦截:", exc)

只依赖标准库 ``urllib``，可在无第三方依赖的 Agent 运行时中直接使用。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class GateClientError(Exception):
    """传输层 / 非门禁拒绝错误（连接失败、4xx 非 403 等）。"""


class GateDenied(GateClientError):
    """门禁拦截（HTTP 403）：写入或命令执行被阶段策略拒绝。"""

    def __init__(self, message: str, status: int = 403, payload: dict | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload or {}
        self.reason = (payload or {}).get("error") or message


class GateClient:
    """与 ``anti_shortcut.sidecar`` 通信的最小客户端。"""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"} if data else {},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            parsed: dict[str, Any] = {}
            if body:
                try:
                    parsed = json.loads(body)
                except ValueError:
                    parsed = {"error": body}
            if exc.code == 403:
                raise GateDenied(
                    parsed.get("error") or "门禁拒绝（HTTP 403）", exc.code, parsed
                ) from exc
            raise GateClientError(f"HTTP {exc.code}: {parsed.get('error') or body}") from exc
        except urllib.error.URLError as exc:
            raise GateClientError(f"无法连接 sidecar: {exc.reason}") from exc

    def state(self) -> dict[str, Any]:
        return self._request("GET", "/api/state")

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        return self._request("POST", "/api/write", {"path": path, "content": content})

    def execute_command(
        self, command: str, cwd: str | None = None, timeout: int | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"command": command}
        if cwd is not None:
            payload["cwd"] = cwd
        if timeout is not None:
            payload["timeout"] = timeout
        return self._request("POST", "/api/exec", payload)

    def advance(self, new_stage: int) -> dict[str, Any]:
        return self._request("POST", "/api/advance", {"new_stage": new_stage})

    def record_test_run(self, exit_code: int, output: str) -> dict[str, Any]:
        return self._request(
            "POST", "/api/test-run", {"exit_code": exit_code, "output": output}
        )

    def record_source_change(self, path: str) -> dict[str, Any]:
        return self._request("POST", "/api/source-change", {"path": path})