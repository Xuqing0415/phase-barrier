# sidecar 入站 mTLS 访问控制示例（v0.21.0）

演示用 **双向 TLS（mTLS）** 保护 phase-barrier sidecar 的 HTTP API：

- 服务端证书：`--tls-cert` / `--tls-key`
- 客户端 CA：`--tls-client-ca`（启用后强制要求客户端证书，`ssl.CERT_REQUIRED`）

未携带受信客户端证书的调用在 **TLS 握手阶段**即被拒绝，门禁 API
（`/api/state`、`/api/audit`、`/api/write`、`/api/exec`、`/api/verify-evidence` 等）整体受保护。

## 快速开始

```bash
pip install cryptography   # 仅示例需要；phase-barrier 核心不依赖
python examples/mtls_sidecar/demo.py
```

## 手工分步

```bash
# 1. 生成证书（默认输出到 examples/mtls_sidecar/certs/）
python examples/mtls_sidecar/generate_certs.py --out examples/mtls_sidecar/certs

# 2. 以 mTLS 启动 sidecar
python -m anti_shortcut sidecar --workspace . \
    --tls-cert examples/mtls_sidecar/certs/server.crt \
    --tls-key examples/mtls_sidecar/certs/server.key \
    --tls-client-ca examples/mtls_sidecar/certs/ca.pem \
    --host 127.0.0.1 --port 8443

# 3. 携带客户端证书访问
python - <<'PY'
from anti_shortcut.proxy_client import GateClient

client = GateClient(
    "https://127.0.0.1:8443",
    ca="examples/mtls_sidecar/certs/ca.pem",
    cert=(
        "examples/mtls_sidecar/certs/client.crt",
        "examples/mtls_sidecar/certs/client.key",
    ),
)
print(client.state())
print(client.audit())
PY
```

不携带客户端证书的 `GateClient` / `curl` 会在 TLS 握手时报错（certificate required）。

## 说明

- `generate_certs.py` 基于 `cryptography` 生成自签 CA / 服务端 / 客户端证书，
  仅供示例 / 测试使用；生产环境请使用正规 PKI 或 Kubernetes cert-manager。
- 远程审计的 mTLS（`audit_remote_*`，出站）见 `examples/mtls_audit/`；
  本示例是 **入站 mTLS**（保护 sidecar API，v0.21.0）。
- K8s 中可将证书以 Secret 挂载，并把 `--tls-*` 参数写入 `gate-sidecar` 容器 args。
