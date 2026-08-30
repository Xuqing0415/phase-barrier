"""mTLS 审计远程推送端到端测试（v0.12.0）。

需要 ``cryptography`` 生成自签证书（已加入 dev extras，CI 可用）；
未安装时整组跳过。
"""
import json
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

cryptography = pytest.importorskip("cryptography")

from anti_shortcut import AntiShortcutSkill  # noqa: E402
from anti_shortcut.remote_audit import RemoteAuditSink  # noqa: E402

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "mtls_audit"
sys.path.insert(0, str(EXAMPLES_DIR))
from generate_certs import generate_cert_bundle  # noqa: E402


@pytest.fixture
def mtls_collector(tmp_path):
    """mTLS 收集端：要求客户端证书，返回 (certs, url, received)。"""
    certs = generate_cert_bundle(tmp_path / "certs")
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body)
            received.extend(payload if isinstance(payload, list) else [payload])
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
    url = f"https://127.0.0.1:{server.server_address[1]}/audit"
    try:
        yield certs, url, received
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_mtls_sink_delivers(mtls_collector):
    certs, url, received = mtls_collector
    sink = RemoteAuditSink(
        url,
        ca_bundle=certs["ca"],
        client_cert=certs["client_cert"],
        client_key=certs["client_key"],
        headers={"X-Tenant": "acme"},
        flush_interval=60,
    )
    try:
        sink.enqueue({"event": "mtls_ok", "n": 1})
        sink.flush()
        assert sink.stats()["failed_batches"] == 0
        assert sink.stats()["sent_events"] == 1
        assert received[0]["event"] == "mtls_ok"
    finally:
        sink.close()


def test_mtls_skill_delivers(mtls_collector, tmp_path):
    certs, url, received = mtls_collector
    skill = AntiShortcutSkill(
        tmp_path,
        config={
            "audit_remote_url": url,
            "audit_remote_ca_bundle": certs["ca"],
            "audit_remote_client_cert": certs["client_cert"],
            "audit_remote_client_key": certs["client_key"],
            "audit_remote_flush_interval": 60,
        },
        user_request="mTLS 测试",
    )
    try:
        skill.logger.info("mtls_skill_event", phase="demo")
        skill.remote_sink.flush()
        names = [e.get("event") for e in received]
        assert "mtls_skill_event" in names
    finally:
        skill.close()


def test_mtls_without_client_cert_fails_and_spools(mtls_collector, tmp_path):
    """无客户端证书时 TLS 握手失败：事件进入持久化 spool（重试耗尽不丢失）。"""
    certs, url, received = mtls_collector
    sink = RemoteAuditSink(
        url,
        ca_bundle=certs["ca"],  # 信任 CA 但未提供客户端证书 -> 服务端拒绝
        retries=0,
        spool_dir=str(tmp_path / "spool"),
        flush_interval=60,
        start_worker=False,
    )
    try:
        sink.enqueue({"event": "no_cert"})
        sink.flush()
        stats = sink.stats()
        assert stats["failed_batches"] == 1
        assert stats["spooled_events"] == 1
        assert received == []
    finally:
        sink.close()
    spool = tmp_path / "spool" / "audit_spool.jsonl"
    assert spool.is_file()
    assert '"no_cert"' in spool.read_text(encoding="utf-8")
