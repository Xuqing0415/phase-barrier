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
- ``GET  /api/audit``                ``{?limit=50&offset=0&since=...&until=...&event=...}`` 查询审计日志（分页 / 时间过滤，v0.21.0）
- ``GET  /api/verify-evidence``      校验证据签名清单（v0.21.0）

v0.9.0：新增 ``--audit-remote-url`` / ``--audit-remote-token``，把审计事件异步
转发到 SIEM / webhook；关闭时自动冲刷远程审计队列。

v0.11.0：新增 mTLS 客户端证书（``--audit-remote-client-cert`` / ``--audit-remote-client-key``）、
自定义请求头（``--audit-remote-header NAME=VALUE``，可多次）与持久化重试队列
（``--audit-remote-spool-dir``，发送失败事件落盘、重启后自动恢复重发）。

部署模板见 ``deploy/k8s/``；本地试运行：:

    python -m anti_shortcut.sidecar --workspace . --port 8080
"""
from __future__ import annotations

import argparse
import json
import os
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .config import load_config
from .interceptors import summarize_test_output
from .proxy import ExecDenied, GateProxy, ProxyError, WriteDenied
from .skill import AntiShortcutSkill


def _parse_event_time(value: Any) -> datetime | None:
    """解析审计日志时间戳（structlog ISO 或 fallback ``%Y-%m-%dT%H:%M:%S%z``）。

    无时区信息时按 UTC 处理，保证与带时区时间戳可比较。
    """
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    m = re.search(r"([+-])(\d{2})(\d{2})$", s)
    if m:
        s = s[: m.start(2)] + m.group(2) + ":" + m.group(3)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class GateSidecar:
    """持有 Skill 实例，并以线程安全方式对外提供门禁操作。"""

    def __init__(
        self,
        workspace: str | Path,
        config: str | Path | dict[str, Any] | None = None,
        user_request: str = "",
    ) -> None:
        self.skill = AntiShortcutSkill(workspace, config=config, user_request=user_request)
        self.proxy = GateProxy(self.skill)
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

    # ---------- 透明代理（v0.17.0） ----------

    def write_file(self, path: str, content: str) -> dict:
        """经门禁写入工作区文件；被拒绝抛 WriteDenied（HTTP 403）。"""
        with self._lock:
            return self.proxy.write_file(path, content)

    def execute_command(
        self, command: str, timeout: int | None = None, cwd: str | None = None
    ) -> dict:
        """经门禁执行 shell 命令并自动记录测试摘要；被拒绝抛 ExecDenied（HTTP 403）。"""
        with self._lock:
            return self.proxy.execute_command(command, timeout=timeout, cwd=cwd)

    def record_source_change(self, path: str) -> dict[str, Any]:
        with self._lock:
            self.skill.state.mark_source_change(path)
        return {"ok": True, "path": path}

    def audit(
        self,
        limit: int = 50,
        offset: int = 0,
        since: str | None = None,
        until: str | None = None,
        event: str | None = None,
    ) -> dict[str, Any]:
        """读取本地审计日志（``.agent_gate/audit.log``），按时间倒序返回事件（v0.20.0+）。

        :param limit: 最多返回条数（1-500，默认 50）
        :param offset: 跳过最近 N 条（分页游标，默认 0，v0.21.0）
        :param since: 可选 ISO 时间戳（含），只返回不早于该时间的事件（v0.21.0）
        :param until: 可选 ISO 时间戳（含），只返回不晚于该时间的事件（v0.21.0）
        :param event: 可选事件名精确过滤（如 ``proxy_write_denied``）
        :return: ``{"ok": True, "count": N, "total": M, "offset": O, "events": [...]}``
        """
        since_dt = _parse_event_time(since) if since is not None else None
        until_dt = _parse_event_time(until) if until is not None else None
        log_file = self.skill.gate_dir / self.skill.config.audit_log_name
        events: list[dict[str, Any]] = []
        if log_file.exists():
            for line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict) or "event" not in record:
                    continue
                if event is not None and record.get("event") != event:
                    continue
                if since_dt is not None or until_dt is not None:
                    ts = _parse_event_time(record.get("timestamp") or record.get("ts"))
                    if ts is None:
                        continue
                    if since_dt is not None and ts < since_dt:
                        continue
                    if until_dt is not None and ts > until_dt:
                        continue
                events.append(record)
        events.reverse()
        sliced = events[offset : offset + limit]
        return {
            "ok": True,
            "count": len(sliced),
            "total": len(events),
            "offset": offset,
            "events": sliced,
        }


    def verify_evidence(self) -> dict[str, Any]:
        """校验证据签名清单与当前工作区文件（evidence_manifest.json，v0.21.0）。

        :return: ``{"ok": bool, "violations": [...], "entries": [...], "signed": bool}``
        """
        try:
            ok, violations = self.skill.verify_evidence()
        except Exception as exc:  # EvidenceManifestError（清单损坏 / 签名不匹配）等
            return {"ok": False, "violations": [str(exc)], "error": str(exc)}
        return {
            "ok": ok,
            "violations": violations,
            "entries": sorted(self.skill.evidence_manifest.entries()),
            "signed": self.skill.evidence_manifest.is_signed(),
        }


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
            elif self.path.startswith("/api/audit"):
                parsed = urlsplit(self.path)
                if parsed.path != "/api/audit":
                    self._send(404, {"error": "not found"})
                    return
                query = parse_qs(parsed.query)
                limit = 50
                if "limit" in query:
                    raw = query["limit"][0]
                    try:
                        limit = int(raw)
                    except ValueError:
                        limit = -1
                    if isinstance(limit, bool) or not 1 <= limit <= 500:
                        self._send(400, {"error": "limit 必须是 1-500 的整数"})
                        return
                offset = 0
                if "offset" in query:
                    raw = query["offset"][0]
                    try:
                        offset = int(raw)
                    except ValueError:
                        offset = -1
                    if isinstance(offset, bool) or offset < 0:
                        self._send(400, {"error": "offset 必须是非负整数"})
                        return
                since = (query.get("since") or [None])[0]
                until = (query.get("until") or [None])[0]
                if since is not None and _parse_event_time(since) is None:
                    self._send(400, {"error": "since 必须是 ISO 时间戳"})
                    return
                if until is not None and _parse_event_time(until) is None:
                    self._send(400, {"error": "until 必须是 ISO 时间戳"})
                    return
                event = (query.get("event") or [None])[0] or None
                self._send(
                    200,
                    sidecar.audit(
                        limit=limit,
                        offset=offset,
                        since=since,
                        until=until,
                        event=event,
                    ),
                )
            elif self.path == "/api/verify-evidence":
                self._send(200, sidecar.verify_evidence())
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path == "/api/advance":
                data = self._read_json()
                new_stage = data.get("new_stage")
                # v0.15.0：拒绝 bool（Python 中 bool 是 int 子类）与越界阶段号
                if (
                    not isinstance(new_stage, int)
                    or isinstance(new_stage, bool)
                    or new_stage < 0
                    or new_stage > 6
                ):
                    self._send(400, {"error": "new_stage 必须是 0-6 的整数"})
                    return
                result = sidecar.advance(new_stage)
                self._send(200 if result.get("success") else 409, result)
            elif self.path == "/api/test-run":
                data = self._read_json()
                exit_code = data.get("exit_code")
                output = data.get("output", "")
                if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                    self._send(400, {"error": "exit_code 必须是整数"})
                    return
                if not isinstance(output, str):
                    self._send(400, {"error": "output 必须是字符串"})
                    return
                self._send(200, sidecar.record_test_run(exit_code, output))
            elif self.path == "/api/source-change":
                data = self._read_json()
                path = data.get("path")
                if not isinstance(path, str) or not path:
                    self._send(400, {"error": "path 必须是非空字符串"})
                    return
                if sidecar.skill._in_gate_dir(path):
                    self._send(400, {"error": "path 不允许指向门禁目录.agent_gate"})
                    return
                self._send(200, sidecar.record_source_change(path))
            elif self.path == "/api/write":
                data = self._read_json()
                path = data.get("path")
                content = data.get("content", "")
                if not isinstance(path, str) or not path.strip():
                    self._send(400, {"error": "path 必须是非空字符串"})
                    return
                if not isinstance(content, str):
                    self._send(400, {"error": "content 必须是字符串"})
                    return
                try:
                    result = sidecar.write_file(path, content)
                except WriteDenied as exc:
                    self._send(403, {"ok": False, "error": exc.reason})
                    return
                except ProxyError as exc:
                    self._send(400, {"ok": False, "error": str(exc)})
                    return
                self._send(200, result)
            elif self.path == "/api/exec":
                data = self._read_json()
                command = data.get("command")
                timeout = data.get("timeout")
                cwd = data.get("cwd")
                if not isinstance(command, str) or not command.strip():
                    self._send(400, {"error": "command 必须是非空字符串"})
                    return
                if timeout is not None and (
                    not isinstance(timeout, int)
                    or isinstance(timeout, bool)
                    or not 1 <= timeout <= 3600
                ):
                    self._send(400, {"error": "timeout 必须是 1-3600 的整数秒"})
                    return
                if cwd is not None and not isinstance(cwd, str):
                    self._send(400, {"error": "cwd 必须是字符串"})
                    return
                try:
                    result = sidecar.execute_command(command, timeout=timeout, cwd=cwd)
                except ExecDenied as exc:
                    self._send(403, {"ok": False, "error": exc.reason})
                    return
                except ProxyError as exc:
                    self._send(400, {"ok": False, "error": str(exc)})
                    return
                self._send(200, result)
            else:
                self._send(404, {"error": "not found"})
    return Handler


def make_server(
    sidecar: GateSidecar,
    host: str = "0.0.0.0",
    port: int = 8080,
    tls_cert: str | None = None,
    tls_key: str | None = None,
    tls_client_ca: str | None = None,
) -> ThreadingHTTPServer:
    """构造 sidecar HTTP 服务器；提供 mTLS 选项时要求客户端证书（v0.21.0）。"""
    server = ThreadingHTTPServer((host, port), make_handler(sidecar))
    if tls_cert or tls_key or tls_client_ca:
        if not (tls_cert and tls_key and tls_client_ca):
            raise ValueError("启用 mTLS 需要同时提供 tls_cert / tls_key / tls_client_ca")
        import ssl

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(tls_cert, tls_key)
        context.load_verify_locations(cafile=tls_client_ca)
        context.verify_mode = ssl.CERT_REQUIRED
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def _merge_config(args: argparse.Namespace) -> Any:
    """合并配置文件 / 命令行 / 环境变量中的远程审计参数。

    优先级：命令行 > 配置文件 > 环境变量（``AUDIT_REMOTE_URL`` / ``AUDIT_REMOTE_TOKEN`` /
    ``AUDIT_REMOTE_CLIENT_CERT`` / ``AUDIT_REMOTE_CLIENT_KEY`` / ``AUDIT_REMOTE_SPOOL_DIR`` /
    ``AUDIT_REMOTE_HEADERS``；K8s 场景用 Secret 注入环境变量即可，无需改 Deployment args）。
    """
    cfg = None
    if args.config:
        cfg = load_config(args.config)
    url = args.audit_remote_url or os.environ.get("AUDIT_REMOTE_URL") or ""
    token = args.audit_remote_token or os.environ.get("AUDIT_REMOTE_TOKEN") or ""
    client_cert = args.audit_remote_client_cert or os.environ.get("AUDIT_REMOTE_CLIENT_CERT") or ""
    client_key = args.audit_remote_client_key or os.environ.get("AUDIT_REMOTE_CLIENT_KEY") or ""
    spool_dir = args.audit_remote_spool_dir or os.environ.get("AUDIT_REMOTE_SPOOL_DIR") or ""

    # 命令行 --audit-remote-header NAME=VALUE 可多次；环境变量 AUDIT_REMOTE_HEADERS 为 JSON 对象
    headers: dict[str, str] = {}
    env_headers = os.environ.get("AUDIT_REMOTE_HEADERS")
    if env_headers:
        try:
            parsed = json.loads(env_headers)
            if isinstance(parsed, dict):
                headers.update({str(k): str(v) for k, v in parsed.items()})
        except ValueError:
            pass
    for item in getattr(args, "audit_remote_headers", None) or []:
        name, sep, value = item.partition("=")
        if sep and name:
            headers[name] = value

    if url or token or client_cert or client_key or spool_dir or headers:
        if cfg is None:
            cfg = {
                "audit_remote_url": url or None,
                "audit_remote_token": token or None,
                "audit_remote_client_cert": client_cert or None,
                "audit_remote_client_key": client_key or None,
                "audit_remote_spool_dir": spool_dir or None,
                "audit_remote_headers": headers,
            }
        else:
            if url:
                cfg.audit_remote_url = url
            if token:
                cfg.audit_remote_token = token
            if client_cert:
                cfg.audit_remote_client_cert = client_cert
            if client_key:
                cfg.audit_remote_client_key = client_key
            if spool_dir:
                cfg.audit_remote_spool_dir = spool_dir
            if headers:
                cfg.audit_remote_headers = {**(cfg.audit_remote_headers or {}), **headers}
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
    parser.add_argument(
        "--audit-remote-client-cert",
        default="",
        help="审计远程推送 mTLS 客户端证书 PEM（可选；也可用环境变量 AUDIT_REMOTE_CLIENT_CERT）",
    )
    parser.add_argument(
        "--audit-remote-client-key",
        default="",
        help="审计远程推送 mTLS 客户端私钥 PEM（可选；也可用环境变量 AUDIT_REMOTE_CLIENT_KEY）",
    )
    parser.add_argument(
        "--audit-remote-header",
        dest="audit_remote_headers",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="审计远程推送自定义请求头（可多次指定；也可用环境变量 AUDIT_REMOTE_HEADERS 传 JSON 对象）",
    )
    parser.add_argument(
        "--audit-remote-spool-dir",
        default="",
        help="审计远程推送持久化重试队列目录（可选；也可用环境变量 AUDIT_REMOTE_SPOOL_DIR）",
    )
    parser.add_argument(
        "--tls-cert", default="", help="mTLS 服务端证书 PEM（v0.21.0，启用后要求客户端证书）"
    )
    parser.add_argument("--tls-key", default="", help="mTLS 服务端私钥 PEM")
    parser.add_argument(
        "--tls-client-ca", default="", help="客户端证书签发 CA（PEM），启用后强制客户端证书"
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
    server = make_server(
        sidecar,
        host=args.host,
        port=args.port,
        tls_cert=args.tls_cert or None,
        tls_key=args.tls_key or None,
        tls_client_ca=args.tls_client_ca or None,
    )
    scheme = "https" if args.tls_cert else "http"
    print(
        f"[sidecar] phase-barrier 门禁服务已启动: {scheme}://{args.host}:{args.port}"
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