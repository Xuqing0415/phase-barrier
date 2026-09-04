# 教程：K8s sidecar 生产加固与 gRPC 接口（v0.39.0 起 gRPC 已实现）

> v0.39.0 起 sidecar 提供与 HTTP API 等价的 **gRPC 服务**（`python -m anti_shortcut.grpc_service`），
> proto 定义见 `anti_shortcut/proto/sidecar.proto`，客户端与 HTTP 共用同一套 GateSidecar 业务
> 逻辑（状态推进、证据校验、透明代理拦截、审计）。HTTP 仍为默认与最小依赖路径；gRPC 为可选
> 增强（`pip install 'phase-barrier[grpc]'`）。

phase-barrier 的 K8s 部署采用 **sidecar 透明代理** 模式：Agent 容器不挂载门禁目录，
文件写入与命令执行都走 sidecar 的门禁 API（HTTP `/api/*` 或 gRPC `PhaseBarrier` 服务），
因此 Agent 无法绕过阶段门禁，也无法篡改状态。

本教程分三部分：现状速览、生产加固清单、以及 **gRPC 接口接入指南**。
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
跨节点访问只允许 mTLS 客户端证书；审计外发是 sidecar -> 审计端点（出站），不是入站。

### 6. 审计闭环

- 开启远程审计，设置 `AUDIT_REMOTE_SPOOL_DIR` 做本地缓冲，避免审计端点抖动丢日志。
- 阶段变更、拦截事件都写入审计；事故复盘时用 `kubectl logs` + 远程审计双查。

## 三、gRPC 接口接入指南（v0.39.0 已实现）

gRPC 服务与 HTTP API 等价（8 个 RPC，语义一一对应），适合对类型安全、流式/长连接、
多路复用有要求的客户端（如 Go/Rust sidecar 代理或生产级 Agent SDK）。

### 1. 服务定义（`anti_shortcut/proto/sidecar.proto`）

`service PhaseBarrier` 提供 8 个 RPC：

| gRPC RPC | 等价 HTTP | 说明 |
|----------|-----------|------|
| `GetState` | `GET /api/state` | 当前阶段 / 完成情况 |
| `Advance` | `POST /api/advance` | 推进阶段（完整证据校验；跳步返回 `FAILED_PRECONDITION`） |
| `RecordTestRun` | `POST /api/test-run` | 上报测试运行（自动解析通过数 / 覆盖率） |
| `RecordSourceChange` | `POST /api/source-change` | 上报源码/测试变更（强制回归） |
| `WriteFile` | `POST /api/write` | 透明代理写入（拦截返回 `PERMISSION_DENIED`） |
| `ExecCommand` | `POST /api/exec` | 透明代理执行命令（拦截返回 `PERMISSION_DENIED`） |
| `VerifyEvidence` | `GET /api/verify-evidence` | 校验证据签名清单 |
| `QueryAudit` | `GET /api/audit` | 审计查询（分页 / 时间过滤 / 事件过滤） |

错误语义与 HTTP 对齐：写入门禁目录、未到阶段写源码/跑测试等被拒返回
`PERMISSION_DENIED`；参数非法（空路径、越界 timeout/limit、非法时间戳）返回
`INVALID_ARGUMENT`；推进未通过证据校验返回 `FAILED_PRECONDITION`。

### 2. 启动 gRPC 服务

```bash
pip install 'phase-barrier[grpc]'

# 独立入口（等价 HTTP sidecar 的业务逻辑）
python -m anti_shortcut.grpc_service --workspace . --host 0.0.0.0 --port 50051 \
  --state-key "$PHASE_BARRIER_HMAC_KEY"
```

mTLS 与 HTTP 模式一致（`--tls-cert` / `--tls-key` / `--tls-client-ca`）；启用
`--tls-client-ca` 后强制校验客户端证书。K8s 中可与 HTTP sidecar 同容器双端口暴露，
或单独作为 gRPC sidecar 容器（详见 [K8s 部署](../k8s.md) 与 Helm `values.yaml`）。

### 3. 客户端示例（Python）

```python
import grpc
from anti_shortcut.proto import sidecar_pb2, sidecar_pb2_grpc

channel = grpc.insecure_channel("phase-barrier:50051")
stub = sidecar_pb2_grpc.PhaseBarrierStub(channel)

state = stub.GetState(sidecar_pb2.GetStateRequest(), timeout=5)
print(state.current_stage, state.stage_name)

try:
    stub.WriteFile(sidecar_pb2.WriteFileRequest(path="fib.py", content="..."), timeout=5)
except grpc.RpcError as exc:
    if exc.code() == grpc.StatusCode.PERMISSION_DENIED:
        print("拦截:", exc.details())  # 与 HTTP 403 同一语义
```

### 4. 版本说明

- v0.39.0 前：gRPC 仅为设计草案（`docs/tutorials` 曾标注“规划中”）。
- v0.39.0：gRPC 服务实现并纳入测试与打包（可选依赖 `phase-barrier[grpc]`）；
  生成代码随包分发（`anti_shortcut/proto/`），重生成脚本见 `scripts/gen_grpc.sh`。