# mTLS 审计远程推送示例（v0.12.0）

演示 phase-barrier 审计事件经 **双向 TLS（mTLS）** 安全送达 SIEM / webhook 收集端：

- 客户端证书：`audit_remote_client_cert` / `audit_remote_client_key`（PEM，成对配置）
- 自定义 CA：`audit_remote_ca_bundle`（自建端点证书不受公开 CA 信任时使用）
- 自定义请求头：`audit_remote_headers`

## 目录

| 文件 | 说明 |
|------|------|
| `generate_certs.py` | 生成自签 CA / 服务端 / 客户端证书（依赖 `cryptography`，仅示例需要） |
| `server.py` | mTLS 收集端点：要求客户端证书，打印收到的审计事件 |
| `demo.py` | 一键端到端演示：生成证书 → 启动收集端 → Skill 发送 → 校验送达 |
| `anti_shortcut_mtls.yaml` | 对应 YAML 配置示例 |

## 快速开始

```bash
pip install cryptography   # 仅示例需要；phase-barrier 核心不依赖
python examples/mtls_audit/demo.py
```

## 手动分步运行

```bash
# 1. 生成证书（默认输出 examples/mtls_audit/certs/）
python examples/mtls_audit/generate_certs.py --out examples/mtls_audit/certs

# 2. 终端 A：启动 mTLS 收集端点
python examples/mtls_audit/server.py \
    --cert examples/mtls_audit/certs/server.crt \
    --key examples/mtls_audit/certs/server.key \
    --ca examples/mtls_audit/certs/ca.pem \
    --port 8443

# 3. 终端 B：用 YAML 配置创建带 mTLS 审计的 Skill
python - <<'PY'
from anti_shortcut import AntiShortcutSkill
skill = AntiShortcutSkill(".", config="examples/mtls_audit/anti_shortcut_mtls.yaml", user_request="mTLS 示例")
skill.logger.info("hello_mtls", note="这条事件应出现在终端 A")
skill.remote_sink.flush()
skill.close()
PY
```

## 配置说明

```yaml
audit_remote_url: https://127.0.0.1:8443/audit
audit_remote_ca_bundle: certs/ca.pem          # 信任自签 CA
audit_remote_client_cert: certs/client.crt    # mTLS 客户端证书
audit_remote_client_key: certs/client.key     # mTLS 客户端私钥
audit_remote_headers:                          # 自定义请求头
  X-Tenant: acme
```

收集端返回 200 即送达；无客户端证书的连接会被服务端拒绝（握手失败），
事件进入持久化重试队列（配置 `audit_remote_spool_dir` 后可跨进程恢复重发）。
