"""远程审计推送（SIEM）测试（v0.9.0）。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from anti_shortcut import AntiShortcutSkill
from anti_shortcut.audit import get_audit_logger
from anti_shortcut.remote_audit import RemoteAuditSink


class _Collector:
    """内存 HTTP 收集端：记录收到的 body 与请求头。"""

    def __init__(self):
        self.bodies: list = []
        self.headers: list[dict] = []
        self._lock = threading.Lock()

    def add(self, body: bytes, headers) -> None:
        with self._lock:
            self.bodies.append(json.loads(body.decode("utf-8")))
            self.headers.append({k: v for k, v in headers.items()})

    def events(self) -> list:
        """把收集到的 body 展开为事件列表（数组自动展开）。"""
        out = []
        for body in self.bodies:
            if isinstance(body, list):
                out.extend(body)
            else:
                out.append(body)
        return out


@pytest.fixture
def collector_server():
    collector = _Collector()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            collector.add(body, self.headers)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/audit"
    try:
        yield collector, url
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_sink_single_event(collector_server):
    collector, url = collector_server
    sink = RemoteAuditSink(url, token="t0k3n", flush_interval=60)
    try:
        sink.enqueue({"event": "stage_advanced", "stage": 2})
        sink.flush()
        assert len(collector.bodies) == 1
        assert collector.bodies[0]["event"] == "stage_advanced"
        assert collector.headers[0].get("Authorization") == "Bearer t0k3n"
    finally:
        sink.close()


def test_sink_batch_multiple(collector_server):
    collector, url = collector_server
    sink = RemoteAuditSink(url, batch_size=3, flush_interval=60)
    try:
        for i in range(3):
            sink.enqueue({"n": i})
        sink.flush()
        assert len(collector.bodies) == 1
        body = collector.bodies[0]
        assert isinstance(body, list) and len(body) == 3
        assert body[2]["n"] == 2
    finally:
        sink.close()


def test_sink_queue_overflow_drops_oldest(collector_server):
    collector, url = collector_server
    sink = RemoteAuditSink(url, max_queue=2, flush_interval=60, start_worker=False)
    try:
        sink.enqueue({"n": 1})
        sink.enqueue({"n": 2})
        sink.enqueue({"n": 3})  # 触发 drop-oldest：丢弃 n=1
        stats = sink.stats()
        assert stats["dropped"] == 1
        sink.flush()
        received = collector.events()
        assert [e["n"] for e in received] == [2, 3]
    finally:
        sink.close()


def test_sink_close_flushes_remaining(collector_server):
    collector, url = collector_server
    sink = RemoteAuditSink(url, flush_interval=60)
    sink.enqueue({"event": "before-close"})
    sink.close()  # 关闭时冲刷剩余事件
    assert collector.events()[0]["event"] == "before-close"


def test_sink_failure_never_crashes():
    sink = RemoteAuditSink("http://127.0.0.1:1/unreachable", timeout=1.0, retries=0, flush_interval=60)
    try:
        sink.enqueue({"event": "a"})
        sink.flush()  # 连接失败：只计数，不抛出
        stats = sink.stats()
        assert stats["failed_batches"] >= 1
        # 失败后仍可继续入队
        sink.enqueue({"event": "b"})
        assert sink.stats()["queued"] == 1
    finally:
        sink.close()


def test_empty_url_rejected():
    with pytest.raises(ValueError):
        RemoteAuditSink("")


def test_logger_forwards_structlog(collector_server, tmp_path):
    collector, url = collector_server
    sink = RemoteAuditSink(url, flush_interval=60)
    try:
        logger = get_audit_logger(tmp_path / "audit.log", remote=sink)
        logger.info("stage_advanced", stage=2)
        sink.flush()
        events = collector.events()
        assert any(e.get("event") == "stage_advanced" and e.get("stage") == 2 for e in events)
    finally:
        sink.close()


def test_logger_forwards_stdlib_fallback(collector_server, tmp_path, monkeypatch):
    import anti_shortcut.audit as audit_mod

    monkeypatch.setattr(audit_mod, "_HAS_STRUCTLOG", False)
    collector, url = collector_server
    sink = RemoteAuditSink(url, flush_interval=60)
    try:
        logger = get_audit_logger(tmp_path / "audit.log", remote=sink)
        logger.info("intercepted", extra={"payload": {"reason": "jump"}})
        sink.flush()
        events = collector.events()
        assert any(e.get("event") == "intercepted" and e.get("reason") == "jump" for e in events)
    finally:
        sink.close()


def test_skill_remote_audit(collector_server, tmp_path):
    collector, url = collector_server
    skill = AntiShortcutSkill(
        tmp_path,
        config={"audit_remote_url": url, "audit_remote_flush_interval": 60},
        user_request="r",
    )
    skill.logger.info("custom_event", foo="bar")
    skill.close()
    events = collector.events()
    names = [e.get("event") for e in events]
    assert "skill_initialized" in names
    assert "gate_dir_policy" in names
    assert "custom_event" in names


# ---------- v0.10.0：重试退避与自定义 CA ----------

@pytest.fixture
def flaky_collector():
    """前 N 次 POST 返回 500，之后正常接收的收集端。"""
    class Box:
        def __init__(self):
            self.bodies: list = []
            self.failures_left = 2
            self._lock = threading.Lock()

    box = Box()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            with box._lock:
                if box.failures_left > 0:
                    box.failures_left -= 1
                    self.send_response(500)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"no")
                    return
            box.bodies.append(json.loads(body.decode("utf-8")))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/audit"
    try:
        yield box, url
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_sink_retry_succeeds_after_transient_failures(flaky_collector):
    box, url = flaky_collector
    sink = RemoteAuditSink(url, retries=3, backoff_factor=0.01, flush_interval=60)
    try:
        sink.enqueue({"event": "retried"})
        sink.flush()
        stats = sink.stats()
        assert stats["sent_batches"] == 1
        assert stats["failed_batches"] == 0
        assert box.bodies[0]["event"] == "retried"
    finally:
        sink.close()


def test_sink_retry_exhausted_counts_failure():
    sink = RemoteAuditSink(
        "http://127.0.0.1:1/unreachable", timeout=1.0, retries=2,
        backoff_factor=0.01, flush_interval=60,
    )
    try:
        sink.enqueue({"event": "a"})
        sink.flush()
        stats = sink.stats()
        assert stats["failed_batches"] == 1
        assert stats["sent_events"] == 0
        assert stats["sent_batches"] == 0
    finally:
        sink.close()


def test_sink_ca_bundle_missing_raises():
    with pytest.raises(ValueError):
        RemoteAuditSink("https://example.com/audit", ca_bundle="no-such-ca.pem")


def test_sink_ca_bundle_invalid_raises(tmp_path):
    bad = tmp_path / "bad-ca.pem"
    bad.write_text("not a pem certificate", encoding="utf-8")
    with pytest.raises(ValueError):
        RemoteAuditSink("https://example.com/audit", ca_bundle=str(bad))


def test_skill_remote_audit_retry_config(tmp_path):
    skill = AntiShortcutSkill(
        tmp_path,
        config={
            "audit_remote_url": "http://127.0.0.1:1/x",
            "audit_remote_retries": 1,
            "audit_remote_backoff_factor": 0.01,
        },
        user_request="r",
    )
    assert skill.remote_sink is not None
    assert skill.remote_sink.retries == 1
    assert skill.remote_sink.backoff_factor == 0.01
    skill.close()
# ---------- v0.11.0：自定义 header / mTLS 客户端证书 / 持久化 spool ----------

def test_sink_custom_headers(collector_server):
    collector, url = collector_server
    sink = RemoteAuditSink(
        url, headers={"X-Tenant": "acme", "X-Source": "phase-barrier"}, flush_interval=60
    )
    try:
        sink.enqueue({"event": "with-headers"})
        sink.flush()
        assert collector.headers[0]["X-Tenant"] == "acme"
        assert collector.headers[0]["X-Source"] == "phase-barrier"
        assert collector.headers[0]["Content-Type"] == "application/json"
    finally:
        sink.close()


def test_sink_token_overrides_authorization_header(collector_server):
    collector, url = collector_server
    sink = RemoteAuditSink(
        url, token="secret", headers={"Authorization": "Bearer other"}, flush_interval=60
    )
    try:
        sink.enqueue({"event": "auth"})
        sink.flush()
        assert collector.headers[0]["Authorization"] == "Bearer secret"
    finally:
        sink.close()


def test_sink_client_cert_missing_file_raises():
    with pytest.raises(ValueError):
        RemoteAuditSink(
            "https://example.com/audit",
            client_cert="no-such-cert.pem",
            client_key="no-such-key.pem",
        )


def test_sink_client_cert_pair_required():
    with pytest.raises(ValueError):
        RemoteAuditSink("https://example.com/audit", client_cert="cert.pem")


def test_sink_client_cert_invalid_pair_raises(tmp_path):
    cert = tmp_path / "client.pem"
    cert.write_text("not a pem", encoding="utf-8")
    key = tmp_path / "client.key"
    key.write_text("not a key", encoding="utf-8")
    with pytest.raises(ValueError):
        RemoteAuditSink(
            "https://example.com/audit", client_cert=str(cert), client_key=str(key)
        )


def test_sink_spool_persists_and_recovers(tmp_path):
    spool = tmp_path / "spool"
    sink = RemoteAuditSink(
        "http://127.0.0.1:1/x", timeout=1.0, retries=0,
        spool_dir=str(spool), flush_interval=60, start_worker=False,
    )
    try:
        sink.enqueue({"event": "lost", "n": 1})
        sink.enqueue({"event": "lost2", "n": 2})
        sink.flush()
        stats = sink.stats()
        assert stats["failed_batches"] == 1
        assert stats["spooled_events"] == 2
    finally:
        sink.close()
    spool_file = spool / "audit_spool.jsonl"
    assert spool_file.is_file()
    assert len(spool_file.read_text(encoding="utf-8").splitlines()) == 2

    sink2 = RemoteAuditSink(
        "http://127.0.0.1:1/x", timeout=1.0, retries=0,
        spool_dir=str(spool), flush_interval=60, start_worker=False,
    )
    try:
        stats2 = sink2.stats()
        assert stats2["recovered_events"] == 2
        assert stats2["queued"] == 2
    finally:
        sink2.close()
    assert not spool_file.exists()


def test_sink_spool_recovery_respects_max_queue(tmp_path):
    spool = tmp_path / "spool"
    sink = RemoteAuditSink(
        "http://127.0.0.1:1/x", timeout=1.0, retries=0,
        spool_dir=str(spool), flush_interval=60, start_worker=False,
    )
    sink.enqueue({"event": "a"})
    sink.flush()
    sink.close()
    sink2 = RemoteAuditSink(
        "http://127.0.0.1:1/x", timeout=1.0, retries=0,
        spool_dir=str(spool), max_queue=1, flush_interval=60, start_worker=False,
    )
    try:
        assert sink2.stats()["recovered_events"] == 1
        assert sink2.stats()["queued"] == 1
    finally:
        sink2.close()


def test_sink_spool_untouched_without_spool_dir(tmp_path):
    sink = RemoteAuditSink(
        "http://127.0.0.1:1/x", timeout=1.0, retries=0, flush_interval=60, start_worker=False
    )
    sink.enqueue({"event": "a"})
    sink.flush()
    sink.close()
    assert not (tmp_path / "audit_spool.jsonl").exists()


def test_skill_remote_audit_v11_config(tmp_path):
    skill = AntiShortcutSkill(
        tmp_path,
        config={
            "audit_remote_url": "http://127.0.0.1:1/x",
            "audit_remote_headers": {"X-Tenant": "acme"},
            "audit_remote_spool_dir": str(tmp_path / "spool"),
        },
        user_request="r",
    )
    assert skill.remote_sink is not None
    assert skill.remote_sink.headers == {"X-Tenant": "acme"}
    assert skill.remote_sink.spool_dir == str(tmp_path / "spool")
    skill.close()
