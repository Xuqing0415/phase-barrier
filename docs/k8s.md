# K8s 部署（Helm + sidecar）

phase-barrier 以 **sidecar 透明代理** 模式运行在 Kubernetes 中：Agent 容器不挂载门禁目录，
所有文件写入与命令执行都经 sidecar 的 HTTP 代理（`/api/write` / `/api/exec`），无法绕过阶段门禁。

## 一键安装

```bash
helm install phase-barrier ./deploy/helm/phase-barrier
```

Helm chart 组件：

- **Deployment**：sidecar + agent 双容器，共享 workspace 卷（agent 写入经 sidecar 校验后落盘）。
- **gate-keeper 一次性 Job**：初始化门禁状态。
- **PVC / emptyDir**：workspace 与 `gate-state` 卷隔离（agent 容器无法写入真实状态）。
- **ConfigMap + Secret**：门禁配置、mTLS 证书、HMAC 密钥、审计推送端点。

## 关键配置（values.yaml）

| 参数 | 说明 |
|------|------|
| `image.repository` / `image.tag` | sidecar 镜像（如 `ghcr.io/xuqing0415/phase-barrier`） |
| `gate.workspace` | 工作区挂载路径 |
| `gate.hmacKey` | 状态签名 HMAC 密钥（生产必配，可用 Secret） |
| `tls.cert` / `tls.key` / `tls.clientCA` | mTLS 证书（客户端证书强制校验） |
| `audit.url` | 审计远程推送端点（SIEM / webhook） |
| `resources` | 容器资源限制 |

## 端到端验证

```bash
bash deploy/k8s/kind-e2e-test.sh
```

脚本创建 kind 集群 → 加载本地镜像 → `helm install` → `kubectl exec` 运行 GateClient
全流程（跳步拦截 ×2 + SOP 推进到交付），并验证 agent 容器无法写入 `.agent_gate`。
该测试已纳入 CI（`e2e-kind` job），每次 PR 自动运行。

## 安全模型

- **状态目录只读**：`gate-state` 卷仅 sidecar 挂载可写，agent 容器无写入路径。
- **mTLS**：agent → sidecar 双向证书认证（`tls-client-ca` 开启后强制客户端证书）。
- **HMAC 状态签名**：`state.json` 每次写入携带 HMAC-SHA256 签名，篡改即拒绝。
- **审计日志**：所有拦截与阶段变更写入结构化日志，可远程推送。
- **暴露面**：默认 `ClusterIP` 仅在集群内可达；如确需对外暴露管理面，可用 Ingress/TLS 终止接入
  sidecar 端口并叠加 mTLS 客户端证书（`sidecar.tls`），不建议直接暴露到公网。

完整 chart 与参数说明见 [deploy/helm/phase-barrier/README.md](https://github.com/Xuqing0415/phase-barrier/tree/main/deploy/helm/phase-barrier)。