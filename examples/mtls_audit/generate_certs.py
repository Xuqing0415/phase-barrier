"""生成 mTLS 审计端点所需的证书（CA / 服务端 / 客户端）。

依赖：``cryptography``（仅示例与端到端测试需要，phase-barrier 核心不依赖）。
用法：:

    python examples/mtls_audit/generate_certs.py --out ./examples/mtls_audit/certs

产物（全部写入 ``--out`` 目录）：
- ``ca.pem``         自签 CA 证书（同时作为客户端信任的 CA）
- ``server.crt`` / ``server.key``   服务端证书 / 私钥（SAN: localhost, 127.0.0.1）
- ``client.crt`` / ``client.key``   客户端证书 / 私钥（供 RemoteAuditSink 使用）
"""
from __future__ import annotations

import argparse
import datetime
import ipaddress
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

__all__ = ["generate_cert_bundle"]


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _csr(name: str, key: rsa.RSAPrivateKey) -> x509.CertificateSigningRequest:
    return (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        )
        .sign(key, hashes.SHA256())
    )


def _issue(
    issuer_cert: x509.Certificate,
    issuer_key: rsa.RSAPrivateKey,
    csr: x509.CertificateSigningRequest,
    *,
    ca: bool,
    san: list[str] | None = None,
) -> x509.Certificate:
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(issuer_cert.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        # OpenSSL 3.x 链构建要求 SKI / AKI 扩展
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(csr.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
            critical=False,
        )
    )
    if ca:
        # OpenSSL 3.x requires a KeyUsage extension on CA certificates
        builder = builder.add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    else:
        builder = builder.add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    if san:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(d) for d in san if d != "127.0.0.1"]
                + [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
    return builder.sign(issuer_key, hashes.SHA256())


def generate_cert_bundle(out_dir: str | Path) -> dict[str, Any]:
    """生成 CA / 服务端 / 客户端证书，返回产物路径映射。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # CA
    ca_key = _key()
    ca_csr = _csr("phase-barrier-test-ca", ca_key)
    ca_cert = _issue(ca_csr, ca_key, ca_csr, ca=True)
    ca_pem = out / "ca.pem"
    ca_pem.write_bytes(
        ca_cert.public_bytes(serialization.Encoding.PEM)
    )

    # 服务端
    server_key = _key()
    server_cert = _issue(
        ca_cert, ca_key, _csr("localhost", server_key),
        ca=False, san=["localhost", "127.0.0.1"],
    )
    server_crt = out / "server.crt"
    server_key_pem = out / "server.key"
    server_crt.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    server_key_pem.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )

    # 客户端
    client_key = _key()
    client_cert = _issue(
        ca_cert, ca_key, _csr("phase-barrier-client", client_key),
        ca=False, san=["client"],
    )
    client_crt = out / "client.crt"
    client_key_pem = out / "client.key"
    client_crt.write_bytes(client_cert.public_bytes(serialization.Encoding.PEM))
    client_key_pem.write_bytes(
        client_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )

    return {
        "ca": str(ca_pem),
        "server_cert": str(server_crt),
        "server_key": str(server_key_pem),
        "client_cert": str(client_crt),
        "client_key": str(client_key_pem),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 mTLS 审计端点证书")
    parser.add_argument("--out", default="examples/mtls_audit/certs", help="证书输出目录")
    args = parser.parse_args(argv)
    paths = generate_cert_bundle(args.out)
    for name, path in paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
