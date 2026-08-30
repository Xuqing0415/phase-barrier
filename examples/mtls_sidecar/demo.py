"""sidecar 入站 mTLS 访问控制示例（v0.21.0）。

演示：
1. 以 mTLS 启动门禁 sidecar（服务端证书 + 强制客户端证书）；
2. 携带客户端证书的 ``GateClient`` 可正常查询状态 / 审计日志；
3. 未携带客户端证书的客户端在 TLS 握手阶段即被拒绝。

运行（需先安装 ``cryptography``，仅示例需要）：

    python examples/mtls_sidecar/demo.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path

# 允许直接以脚本方式运行（repo 根目录加入 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from generate_certs import generate_cert_bundle  # noqa: E402  （本目录工具）

from anti_shortcut.proxy_client import GateClient, GateClientError  # noqa: E402
from anti_shortcut.sidecar import GateSidecar, make_server  # noqa: E402


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="pb-mtls-sidecar-"))
    ws = workdir / "ws"
    ws.mkdir()
    certs = generate_cert_bundle(workdir / "certs")

    sidecar = GateSidecar(ws, user_request="mTLS sidecar 示例")
    server = make_server(
        sidecar,
        host="127.0.0.1",
        port=0,
        tls_cert=certs["server_cert"],
        tls_key=certs["server_key"],
        tls_client_ca=certs["ca"],
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "https://127.0.0.1:%d" % server.server_address[1]

    try:
        print(f"[demo] sidecar mTLS 已启动: {base}")

        # 1) 无客户端证书 -> TLS 握手被拒
        bare = GateClient(base, ca=certs["ca"])
        try:
            bare.state()
            print("[demo] FAIL: 无客户端证书竟然通过了")
            return 1
        except GateClientError as exc:
            print(f"[demo] 无客户端证书被拒绝（符合预期）: {type(exc).__name__}: {exc}")

        # 2) 携带客户端证书 -> 正常访问门禁 API
        client = GateClient(
            base,
            ca=certs["ca"],
            cert=(certs["client_cert"], certs["client_key"]),
        )
        print("[demo] GET /api/state ->", json.dumps(client.state(), ensure_ascii=False))
        try:
            client.write_file("fib.py", "def fib(n): return n")
        except GateClientError as exc:
            print(f"[demo] 阶段 0 写实现代码被门禁拒绝（符合预期）: {exc}")
        audit = client.audit(event="proxy_write_denied")
        print(f"[demo] GET /api/audit?event=proxy_write_denied -> count={audit['count']}")
        print("[demo] OK：mTLS 访问控制 + 阶段门禁 + 审计查询全链路可用")
        return 0
    finally:
        server.shutdown()
        server.server_close()
        sidecar.skill.close()


if __name__ == "__main__":
    raise SystemExit(main())
