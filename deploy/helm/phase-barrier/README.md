# phase-barrier Helm chart（v0.27.0）

一键部署 phase-barrier 门禁 sidecar + 编码 Agent 到 Kubernetes：Agent 与 sidecar 共享
工作区卷，门禁状态卷（`.agent_gate`）由 sidecar 独占挂载，Agent 只能通过
`/api/write` / `/api/exec` 透明代理写入文件与执行命令，无法绕过阶段门禁。

## 快速开始（本地 / e2e）

```bash
helm install phase-barrier ./deploy/helm/phase-barrier   -f deploy/k8s/e2e-values.yaml   --namespace phase-barrier-demo --create-namespace   --wait
```

端到端验证（kind 集群）：

```bash
bash deploy/k8s/kind-e2e-test.sh
```

## 生产部署

```bash
helm install phase-barrier ./deploy/helm/phase-barrier   --namespace phase-barrier-demo --create-namespace   --set persistence.type=pvc   --set sidecar.hmac.enabled=true   --set sidecar.hmac.secretName=pb-secrets   --set image.repository=<registry>/phase-barrier   --set image.tag=<version>
```

- `persistence.type=pvc`：使用 PVC（需集群有默认 StorageClass）；`emptyDir` 适合本地验证。
- 启用状态签名：`sidecar.hmac.enabled=true`，密钥经 Secret 注入（`PHASE_BARRIER_HMAC_KEY`）。
- 启用 mTLS：`sidecar.tls.enabled=true` + `sidecar.tls.secretName`（含 `tls.crt` / `tls.key` / `ca.crt`）。
- 启用审计推送：`sidecar.audit.enabled=true` + `url`（SIEM / webhook）。
- 自定义门禁配置：`sidecar.configYaml`（YAML 字符串，生成 ConfigMap 并经 `--config` 加载）。

## 主要参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `image.repository` / `image.tag` | `ghcr.io/xuqing0415/phase-barrier-demo` / `v0.27.0` | sidecar 镜像 |
| `sidecar.port` / `sidecar.host` | `8080` / `0.0.0.0` | 监听端口与地址 |
| `sidecar.userRequest` | `""` | 阶段 0 需求原文 |
| `sidecar.configYaml` | `""` | 内联 gate 配置（ConfigMap） |
| `sidecar.hmac.enabled` | `false` | 状态签名（HMAC） |
| `sidecar.audit.enabled` | `false` | 审计远程推送 |
| `sidecar.tls.enabled` | `false` | sidecar 入站 mTLS |
| `agent.enabled` / `agent.command` | `true` / 挂起占位 | 编码 Agent 容器 |
| `gatekeeper.enabled` | `false` | 预置交付态的一次性 Job |
| `persistence.type` | `emptyDir` | `emptyDir` / `pvc` |
| `service.type` / `service.port` | `ClusterIP` / `8080` | Service 暴露方式 |
