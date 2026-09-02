# 教程：K8s sidecar 生产加固与 gRPC 演进

phase-barrier 的 K8s 部署采用 **sidecar 透明代理** 模式：Agent 容器不挂载门禁目录，
文件写入与命令执行都走 sidecar 的 HTTP API（`/api/write`、`/api/exec`、`/api/advance`、
`/api/state`、`/api/audit`），因此 Agent 无法绕过阶段门禁，也无法篡改状态。

本教程分三部分：现状速览、生产加固清单、以及 README 长期规划中的 **gRPC 演进方案**。

## 一、现状速览

```bash
helm install phase-barrier ./deploy/helm/phase-barrier
bash deploy/k8s/kind-e2e-test.sh   # kind 端到端验证（CI e2e-kind job 每次 PR 自动跑）
```

架构要点：

- **Deployment**：sidecar + agent 双容器共享 workspace 卷；sidecar 独占 `gate-state` 卷。
- **gate-keeper Job**：初始化门禁状态。
- **ConfigMap + Secret**：门禁配置、mTLS 证书、HMAC 密钥、审计端点。
- **审计**：`AUDIT_REMOTE_URL` / `AUDIT_REMOTE_TOKEN` / `AUDIT_REMOTE_HEADERS` 等环境变量
  注入远程审计（SIEM / webhook），支持 spool 缓冲（`AUDIT_REMOTE_SPOOL_DIR`）。

## 二、生产加固清单

### 1. 强制 mTLS

```yaml
tls:
  cert: <server.crt>
  key:  <server.key>
  clientCA: <client-ca.crt>   # 客户端证书校验：没有合法证书的调用直接被拒
```

`GateClient` 侧携带客户端证书：

```python
from anti_shortcut.proxy_client import GateClient

gate = GateClient(
    "https://phase-barrier:8080",
    cert=("agent.crt", "agent.key"),
    ca="ca.crt",
)
```

### 2. 密钥与配置分离

- HMAC 密钥、mTLS 私钥、审计 token 一律放 **Secret**，不要写进 `values.yaml` 明文。
- 审计端点可用 Secret 注入 `AUDIT_REMOTE_URL` / `AUDIT_REMOTE_TOKEN`，无需改 Deployment args。

### 3. 状态目录隔离

- `gate-state` 卷只挂载给 sidecar，Agent 容器**不挂载**，从根上杜绝状态篡改。
- 即使 Agent 在 workspace 卷写一个同名 `.agent_gate/`，sidecar 读的是自己的卷，
  真实状态不受影响（e2e 用例已验证此隔离）。

### 4. 资源限制与健康检查

```yaml
resources:
  requests: {cpu: 100m, memory: 128Mi}
  limits:   {cpu: "1", memory: 512Mi}
```

给 sidecar 配 `readinessProbe` / `livenessProbe`（探活 `/api/state`），
避免 Agent 在 sidecar 未就绪时把写入失败误判为业务错误。

### 5. 不要暴露 Ingress

sidecar 是 Pod 内通信组件，**不应**通过 Ingress/NodePort 暴露到集群外。
跨节点访问只允许 mTLS 客户端证书；审计外发是 sidecar → 审计端点（出站），不是入站。

### 6. 审计闭环

- 开启远程审计，设置 `AUDIT_REMOTE_SPOOL_DIR` 做本地缓冲，避免审计端点抖动丢日志。
- 阶段变更、拦截事件都写入审计；事故复盘时用 `kubectl logs` + 远程审计双查。

## 三、gRPC 演进方案（Roadmap）

当前 HTTP 透明代理已满足功能需求；README 长期规划中的 **gRPC** 主要解决三类问题：

1. **类型化接口**：`/api/write` 的请求/响应是自由 JSON，字段拼错只能靠运行时才发现；
   gRPC + proto 在编译期约束请求结构。
2. **连接复用与流式**：Agent 高频调用（写文件、跑测试）可复用一条 HTTP/2 连接，
   长任务（`exec`）可用服务端流实时回传输出。
3. **性能**：HTTP/2 多路复用减少握手开销；`benchmarks/bench.py` 的 p95 指标可作前后对比基线。

### 设计草案

```proto
syntax = "proto3";

service PhaseBarrier {
  rpc WriteFile(WriteFileRequest) returns (WriteFileReply);   // /api/write 等价
  rpc ExecCommand(ExecCommandRequest) returns (stream ExecChunk); // /api/exec 流式
  rpc Advance(AdvanceRequest) returns (AdvanceReply);         // /api/advance
  rpc GetState(GetStateRequest) returns (GetStateReply);      // /api/state
}
```

迁移建议（不破坏现有 HTTP 客户端）：

- 新增 `grpc` 监听端口，与 HTTP 并存一个版本周期；
- `GateClient` 增加传输层抽象：`base_url` 不变，内部自动选择 gRPC（可用时）或 HTTP；
- mTLS 证书体系复用（gRPC TLS 配置与 HTTP 一致），Secret 无需变更；
- 先在 kind e2e 中加 gRPC 冒烟用例，全绿后再默认切 gRPC。

### 全链路加固要点

信任链：`Agent → GateClient(mTLS) → sidecar(校验+审计) → 文件系统/执行环境`

| 环节 | 风险 | 对策 |
|------|------|------|
| Agent → sidecar | 未授权调用 | mTLS 客户端证书（`tls-client-ca`） |
| sidecar 校验 | 证据伪造 | AST/启发式校验 + HMAC 状态签名 + 哈希清单 |
| sidecar 执行 | 命令注入 | 拦截规则（`no_shell_injection`）+ 白名单命令模式 |
| 审计 | 日志丢失/篡改 | 远程推送 + spool 缓冲 + 结构化为 JSON |