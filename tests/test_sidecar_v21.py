"""v0.21.0 测试：审计分页 / 时间过滤、verify-evidence 远程校验、sidecar 入站 mTLS。

需要 ``cryptography`` 生成测试证书（已加入 dev extras，CI 已安装）；未安装时跳过 mTLS 用例。
"""
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

cryptography = pytest.importorskip("cryptography")

from anti_shortcut.__main__ import main  # noqa: E402
from anti_shortcut.proxy_client import GateClient, GateClientError  # noqa: E402
from anti_shortcut.sidecar import GateSidecar, make_handler, make_server  # noqa: E402
from conftest import SPEC, USER_REQUEST  # noqa: E402

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "mtls_sidecar"
sys.path.insert(0, str(EXAMPLES_DIR))
from generate_certs import generate_cert_bundle  # noqa: E402


def _make_sidecar(ws, config=None):
    return GateSidecar(ws, config=config, user_request=USER_REQUEST)


def _serve(sidecar):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(sidecar))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


def _get(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _stop(server):
    server.shutdown()
    server.server_close()


def _write_notes(sidecar, n):
    for i in range(n):
        sidecar.write_file(f"notes{i}.md", f"# note {i}")


# ---------- 审计分页 ----------


def test_audit_offset_pagination(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = _make_sidecar(ws)
    _write_notes(sidecar, 4)
    result = sidecar.audit(limit=2, offset=1)
    assert result["count"] == 2
    assert result["offset"] == 1
    assert result["total"] >= 4
    assert result["events"][0]["path"].endswith("notes2.md")
    assert result["events"][1]["path"].endswith("notes1.md")
    # 越过末尾 -> 空页，total 不变
    result = sidecar.audit(offset=100)
    assert result["count"] == 0
    assert result["total"] >= 4
    assert result["events"] == []


def test_http_audit_offset_and_pagination(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = _make_sidecar(ws)
    server, base = _serve(sidecar)
    try:
        for bad in ("-1", "abc", "1.5"):
            code, body = _get(base, "/api/audit?offset=" + bad)
            assert code == 400, bad
            assert "offset" in body.get("error", "")
        code, body = _get(base, "/api/audit?offset=0&limit=1")
        assert code == 200
        assert body["offset"] == 0 and len(body["events"]) <= 1
    finally:
        _stop(server)
        sidecar.skill.close()


# ---------- 审计时间范围过滤 ----------


def test_audit_since_until_filter(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = _make_sidecar(ws)
    sidecar.write_file("notes0.md", "# a")
    ts0 = next(
        e.get("timestamp") or e.get("ts") for e in sidecar.audit()["events"] if e["path"].endswith("notes0.md")
    )
    time.sleep(1.1)  # 兜底：fallback 日志时间戳为秒级精度
    sidecar.write_file("notes1.md", "# b")
    ts1 = next(
        e.get("timestamp") or e.get("ts") for e in sidecar.audit()["events"] if e["path"].endswith("notes1.md")
    )
    assert ts1 != ts0

    r = sidecar.audit(since=ts0)
    paths = [e["path"] for e in r["events"] if "notes" in e.get("path", "")]
    assert any(p.endswith("notes0.md") for p in paths)
    assert any(p.endswith("notes1.md") for p in paths)

    r = sidecar.audit(since=ts1)
    paths = [e["path"] for e in r["events"] if "notes" in e.get("path", "")]
    assert paths and all(p.endswith("notes1.md") for p in paths)

    r = sidecar.audit(until=ts0)
    paths = [e["path"] for e in r["events"] if "notes" in e.get("path", "")]
    assert paths and all(p.endswith("notes0.md") for p in paths)


def test_http_audit_since_until_validation(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = _make_sidecar(ws)
    server, base = _serve(sidecar)
    try:
        code, body = _get(base, "/api/audit?since=garbage")
        assert code == 400 and "since" in body.get("error", "")
        code, body = _get(base, "/api/audit?until=not-a-date")
        assert code == 400 and "until" in body.get("error", "")
        code, body = _get(base, "/api/audit?since=2026-01-01T00:00:00Z&event=skill_initialized")
        assert code == 200
        assert all(e["event"] == "skill_initialized" for e in body["events"])
    finally:
        _stop(server)
        sidecar.skill.close()


# ---------- verify-evidence 远程校验 ----------


def test_verify_evidence_fresh_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = _make_sidecar(ws)
    result = sidecar.verify_evidence()
    assert result["ok"] is False
    assert any("证据清单为空" in v for v in result["violations"])


def test_verify_evidence_after_advance(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = _make_sidecar(ws)
    sidecar.write_file("spec.md", SPEC)
    adv = sidecar.advance(2)
    assert adv.get("success") is True
    result = sidecar.verify_evidence()
    assert result["ok"] is True
    assert "spec.md" in result["entries"]


def test_http_and_client_verify_evidence(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = _make_sidecar(ws)
    server, base = _serve(sidecar)
    client = GateClient(base)
    try:
        code, body = _get(base, "/api/verify-evidence")
        assert code == 200 and body["ok"] is False
        result = client.verify_evidence()
        assert result["ok"] is False
        assert "violations" in result and "entries" in result and "signed" in result
    finally:
        _stop(server)
        sidecar.skill.close()


# ---------- sidecar 入站 mTLS ----------


def test_sidecar_mtls_requires_client_cert(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    certs = generate_cert_bundle(tmp_path / "certs")
    sidecar = _make_sidecar(ws)
    server = make_server(
        sidecar,
        host="127.0.0.1",
        port=0,
        tls_cert=str(certs["server_cert"]),
        tls_key=str(certs["server_key"]),
        tls_client_ca=str(certs["ca"]),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "https://127.0.0.1:%d" % server.server_address[1]
    try:
        client = GateClient(
            base,
            ca=str(certs["ca"]),
            cert=(str(certs["client_cert"]), str(certs["client_key"])),
        )
        state = client.state()
        assert "current_stage" in state
        audit = client.audit(event="skill_initialized")
        assert audit["ok"] is True and audit["count"] >= 1
        # 无客户端证书：TLS 握手被拒
        bare = GateClient(base, ca=str(certs["ca"]))
        with pytest.raises(GateClientError):
            bare.state()
        # 不信任服务端 CA：握手被拒
        untrusted = GateClient(
            base, cert=(str(certs["client_cert"]), str(certs["client_key"]))
        )
        with pytest.raises(GateClientError):
            untrusted.state()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        sidecar.skill.close()


def test_make_server_requires_full_tls_args(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    certs = generate_cert_bundle(tmp_path / "certs")
    sidecar = _make_sidecar(ws)
    with pytest.raises(ValueError):
        make_server(
            sidecar,
            tls_cert=str(certs["server_cert"]),
            tls_key=str(certs["server_key"]),
            tls_client_ca=None,
        )


# ---------- CLI sidecar TLS 参数 ----------


def test_cli_sidecar_tls_args_present(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["sidecar", "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "--tls-cert" in out and "--tls-key" in out and "--tls-client-ca" in out
