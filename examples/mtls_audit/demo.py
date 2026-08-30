"""mTLS 端到端演示：启动收集端点 -> 创建带 mTLS 配置的 Skill -> 记录事件 -> 校验送达。

用法（自动生成证书并演示）：:

    python examples/mtls_audit/demo.py

先决条件：``pip install cryptography``（仅演示需要）。
"""
from __future__ import annotations

import json
import os
import shutil
import ssl
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from generate_certs import generate_cert_bundle

from anti_shortcut import AntiShortcutSkill

BASE = Path(__file__).resolve().parent


def _workdir(prefix: str) -> Path:
    """在系统临时目录创建可写子目录（mkdtemp 目录在部分受限环境下不可写，用完自动清理）。"""
    d = Path(tempfile.gettempdir()) / f"{prefix}-{os.getpid()}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_demo() -> int:
    # 证书写入临时目录，避免污染仓库
    cert_dir = _workdir("phase-barrier-mtls-certs")
    certs = generate_cert_bundle(cert_dir)

    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            received.extend(body if isinstance(body, list) else [body])
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certs["server_cert"], certs["server_key"])
    context.load_verify_locations(cafile=certs["ca"])
    context.verify_mode = ssl.CERT_REQUIRED
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    config = {
        "audit_remote_url": f"https://127.0.0.1:{port}/audit",
        "audit_remote_ca_bundle": certs["ca"],
        "audit_remote_client_cert": certs["client_cert"],
        "audit_remote_client_key": certs["client_key"],
        "audit_remote_headers": {"X-Tenant": "acme"},
        "audit_remote_flush_interval": 60,
    }
    workspace = _workdir("phase-barrier-mtls")
    skill = AntiShortcutSkill(workspace, config=config, user_request="mTLS 演示")
    try:
        skill.logger.info("mtls_demo_event", note="审计事件经 mTLS 送达")
        skill.remote_sink.flush()
    finally:
        skill.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        shutil.rmtree(cert_dir, ignore_errors=True)
        shutil.rmtree(workspace, ignore_errors=True)

    names = [e.get("event") for e in received]
    print("received events:", names)
    assert "mtls_demo_event" in names, "mTLS 送达失败"
    print("OK: mTLS 审计端到端演示通过（临时证书已自动清理）")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_demo())
