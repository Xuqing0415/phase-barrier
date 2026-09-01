# Kubernetes 部署：agent + gate-sidecar（v0.7.0）

把阶段门禁从“进程内工具包装”升级到 **Pod 内进程级隔离**：门禁状态
（`.agent_gate`）存放在独立 PVC 上，只有 sidecar / gate-keeper 挂载，
编码 Agent 容器完全不挂载门禁目录，只能通过 sidecar 的 HTTP API
查询 / 推进阶段——即使 Agent 绕过工具包装直接操作文件系统，也无法篡改状态机。

## 拓扑

```mermaid
flowchart LR
    A[Job gate-keeper<br/>初始化 .agent_gate] -->|写入 gate PVC| G[(phase-barrier-gate)]
    W[(phase-barrier-workspace)] --> B
    B[Deployment agent<br/>容器1: agent 编码<br/>容器2: gate-sidecar] --> G
    B -.HTTP /api/state /api/advance.-> B
    B -.HTTP /api/test-run /api/source-change.-> B
    B -.HTTP /api/write /api/exec(v0.17.0) /api/audit(v0.20.0).-> B
```

- `phase-barrier-workspace`：spec / 测试 / 实现代码，agent 与 sidecar 共享（sidecar 校验证据文件）。
- `phase-barrier-gate`：`.agent_gate`（state.json + audit.log），**只有** gate-keeper 与 sidecar 挂载。
- `gate-keeper` Job：对 gate PVC 可写，运行 `deploy/seed_gate.py` 完成一次完整门禁流程，
  生成可审计的初始交付态；后续真实 Agent 任务可复用同一状态机继续推进（或清空重来）。
- `gate-sidecar`：`python -m anti_shortcut sidecar --workspace /workspace --port 8080`（v0.20.0 统一 CLI 入口），
  提供 `GET /api/state`、`POST /api/advance`、`POST /api/test-run`、`POST /api/source-change`、
  `POST /api/write`、`POST /api/exec`（v0.17.0 透明代理）、`GET /api/audit`（v0.20.0 审计查询）。
- agent 容器：只挂载 workspace；写代码后通过 localhost 调用 sidecar API 推进阶段。


## Helm 一键部署（v0.27.0）

推荐直接使用 Helm chart（`deploy/helm/phase-barrier/`），一条命令部署 sidecar + agent：

```bash
helm install pb ./deploy/helm/phase-barrier --namespace phase-barrier-demo --create-namespace
# 自定义：helm upgrade pb ./deploy/helm/phase-barrier -f my-values.yaml
```

- `values.yaml` 支持：镜像（`image` / `agent.image`）、存储（`persistence.type: emptyDir | pvc`）、
  HMAC 状态签名（`sidecar.hmac`）、mTLS（`sidecar.tls`）、审计远程推送（`sidecar.audit`）、
  内联门禁配置（`sidecar.configYaml`，挂载为 `/workspace/gate.yaml`）、
  一次性 gate-keeper Job（`gatekeeper.enabled`）、资源限制与就绪探针。
- 默认 sidecar 容器命令为 `python -m anti_shortcut sidecar --workspace /workspace --port 8080`；
  agent 容器与 sidecar 共享 `workspace` 卷，`.agent_gate` 单独挂载（`gate-state` 卷），
  agent 无写入权限。
- 端到端验证（需 docker + kind + helm + kubectl）：

  ```bash
  bash deploy/k8s/kind-e2e-test.sh
  ```

  该脚本已在 CI（`e2e-kind` job，ubuntu）中运行：kind 建集群 → 加载本地镜像 →
  `helm install` → `kubectl exec` 运行 GateClient 全流程（跳步拦截 + SOP 推进到交付），
  并断言 agent 容器无法写入 `.agent_gate/state.json`。
- 更多参数说明见 `deploy/helm/phase-barrier/README.md`。

## 快速开始（kind / minikube）

1. 构建并推送镜像（用仓库根 `deploy/Dockerfile`）：

   ```bash
   docker build -f deploy/Dockerfile -t <registry>/phase-barrier:0.17.0 .
   docker push <registry>/phase-barrier:0.17.0
   # kind 本地集群可直接 load：kind load docker-image <registry>/phase-barrier:0.17.0
   ```

2. 把 `gate-keeper.yaml` / `gate-sidecar.yaml` 中的镜像地址替换为你的镜像。

3. 启动集群并应用模板：

   ```bash
   kind create cluster            # 或 minikube start
   kubectl apply -f deploy/k8s/
   ```

4. 等待 gate-keeper 完成、agent Pod 就绪：

   ```bash
   kubectl get job phase-barrier-gate-keeper
   kubectl rollout status deploy/agent
   kubectl get pods
   ```

5. 验证 sidecar API 与门禁隔离：

   ```bash
   POD=$(kubectl get pod -l app=phase-barrier-agent -o jsonpath='{.items[0].metadata.name}')
   # 阶段查询（gate-keeper 已推进到交付态，期望 current_stage=6）
   kubectl exec "$POD" -c agent -- sh -c 'curl -s localhost:8080/api/state'
   # 门禁目录对 agent 不可见（不会挂载 gate PVC）
   kubectl exec "$POD" -c agent -- ls /workspace/.agent_gate   # 期望：No such file or directory
   # 跳跃阶段被拒绝（当前已是 6，advance 返回 409）
   kubectl exec "$POD" -c agent -- sh -c \
     'curl -s -X POST localhost:8080/api/advance -H "Content-Type: application/json" -d "{\"new_stage\": 5}"'
   ```

   > 若 agent 镜像没有 `curl`，可在同一 Pod 内用 `python -c` 调用，或在
   > `gate-sidecar` 容器内执行 `kubectl exec -it "$POD" -c gate-sidecar -- python -m anti_shortcut sidecar --help`。

## 透明代理（v0.17.0）

v0.16.0 之前，sidecar 只拦截“阶段推进”，Agent 仍需自己写文件 / 跑测试命令，
只能依赖 Agent 进程内的工具包装。v0.17.0 新增两个透明代理端点，把门禁下沉到
文件系统层，Agent 容器内即使没有 / 绕开了工具包装，也无法跳过阶段：

- `POST /api/write` `{"path": "fib.py", "content": "..."}`：
  - 路径必须解析在工作区内（拒绝 `../` 越界与绝对路径逃逸）；
  - 拒绝写入门禁目录 `.agent_gate`；
  - 按当前阶段拦截 test / source / other 文件（与 `AntiShortcutSkill.check_write_permission` 同策略）；
  - 通过后写入文件并记录源码变更时间戳。
- `POST /api/exec` `{"command": "pytest -q", "timeout": 120}`：
  - 按阶段拦截测试命令（阶段 < 3 拒绝）与访问门禁目录的 shell 命令；
  - 通过后在共享工作区执行（`cwd` 限定工作区内，支持超时并终止进程树）；
  - 若是测试命令，自动解析输出并把测试摘要写入状态机（无需再单独调用 `/api/test-run`）。
- Agent 侧客户端：`anti_shortcut.proxy_client.GateClient`（仅标准库 urllib），
  把 `write_file` / `execute_command` 重定向到这两个端点即可，被拦截时抛 `GateDenied`。
  最小示例见 `examples/k8s_proxy/`。

## 新任务流程（agent 接入协议）

1. Agent 启动后先 `GET /api/state` 确认初始阶段（新状态机为 1：Spec 设计）。
2. 在 `/workspace` 写 `spec.md` → `POST /api/advance {"new_stage": 2}`。
3. 写测试 → `POST /api/advance {"new_stage": 3}`；写实现 → `POST /api/advance {"new_stage": 4}`。
4. 自己运行测试命令后，把结果上报：`POST /api/test-run {"exit_code": 0, "output": "<命令输出>"}`
   （sidecar 会按语言适配器解析摘要 / 覆盖率，并写入状态机）。
5. `POST /api/advance {"new_stage": 5}`：一次通过则直接交付（阶段 6），否则进入阶段 5 修复回归。
6. 每次修改源码 / 测试后 `POST /api/source-change {"path": "..."}`，修复后重跑测试并重新上报。

需要覆盖率门禁时，把 `gate-sidecar.yaml` 中注释的 `--config /workspace/gate.yaml` 打开，
在配置里写 `coverage_threshold: 80`，测试命令带上覆盖率参数（如 `pytest --cov --cov-report=term-missing`）。

## 安全说明

- **门禁目录隔离**：agent 容器不挂载 `phase-barrier-gate`，文件系统层面无法接触状态文件；
  sidecar 是唯一可信写入方。
- **只读加固（可选）**：若把 gate PVC 的 `accessModes` 调整为 `ReadOnlyMany` 且 sidecar 以
  `readOnly: true` 挂载，则所有写入只能来自 gate-keeper 等初始化 Job——适合“先初始化、后只读校验”的
  门禁模式（此时 advance 只读校验证据，不修改状态，需要把推进操作也交给 gate-keeper Job 完成）。
- **网络**：sidecar Service 仅暴露在集群内；生产环境建议用 NetworkPolicy 限制只有 agent Pod 可访问。
- **入站 mTLS（v0.21.0）**：sidecar 支持 `--tls-cert` / `--tls-key` / `--tls-client-ca`，要求客户端证书后才接受 `/api/*` 调用；配合 NetworkPolicy 可进一步限制访问来源。
- **镜像签名**：配合 sigstore / cosign 对 sidecar 镜像签名，防止供应链投毒（见仓库 Roadmap）。
- **状态签名（v0.8.0）**：为 sidecar 注入 `PHASE_BARRIER_HMAC_KEY`（Secret → env，见
  `gate-sidecar.yaml` 注释），state.json 即带 HMAC-SHA256 签名；Agent 篡改状态后
  sidecar 拒绝加载并明确报错。

- **审计远程推送（v0.9.0）**：sidecar 支持 `--audit-remote-url` / `--audit-remote-token`，
  也可用环境变量 `AUDIT_REMOTE_URL` / `AUDIT_REMOTE_TOKEN`（Secret 注入即可，无需改 args），
  审计事件异步批量转发到 SIEM / webhook，失败只计数、不影响门禁。
- **证据签名（v0.9.0）**：sidecar 推进阶段时写入 `evidence_manifest.json`；CI 可用
  `python -m anti_shortcut verify-evidence --workspace /workspace` 校验证据未被事后篡改。
- **密钥轮换（v0.9.0）**：`python -m anti_shortcut rotate-key --workspace /workspace --from <旧> --to <新> [--keep-old]`
  无中断轮换 HMAC 密钥（宽限期双密钥）。