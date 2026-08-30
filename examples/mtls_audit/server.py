"""mTLS 审计收集端点（示例）：要求客户端证书，打印收到的审计事件。

用法（先运行 generate_certs.py 生成证书）：:

    python examples/mtls_audit/server.py \
        --cert examples/mtls_audit/certs/server.crt \
        --key examples/mtls_audit/certs/server.key \
        --ca examples/mtls_audit/certs/ca.pem \
        --port 8443
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class AuditHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # 关闭默认访问日志
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = body
        # 展示客户端证书信息，证明 mTLS 双向认证生效
        peer = self.connection.getpeercert()
        subject = dict(x[0] for x in peer.get("subject", [])) if peer else {}
        print(
            json.dumps(
                {
                    "event_count": len(payload) if isinstance(payload, list) else 1,
                    "client_cn": subject.get("commonName", ""),
                    "payload": payload,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mTLS 审计收集端点示例")
    parser.add_argument("--cert", required=True, help="服务端证书 PEM")
    parser.add_argument("--key", required=True, help="服务端私钥 PEM")
    parser.add_argument("--ca", required=True, help="信任的 CA（客户端证书签发者）PEM")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8443, help="监听端口")
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), AuditHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(args.cert, args.key)
    context.load_verify_locations(cafile=args.ca)
    context.verify_mode = ssl.CERT_REQUIRED  # 强制客户端证书
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(
        f"[mtls-server] 监听 https://{args.host}:{args.port}/audit（要求客户端证书）",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
