# 与主流 Agent 框架集成（v0.27.0）

phase-barrier 提供两种接入面，可单独或叠加使用：

1. **编排器钩子 SDK（推荐）**：平台在任务启动 / 阶段切换钩子调用 `PhaseBarrier`
   （见 `examples/orchestrator_hooks/`），不修改 Agent 内部工具；
2. **工具级透明代理**：Agent 的文件写入 / 命令执行改走 `GateClient`（sidecar
   `/api/write`、`/api/exec`），在文件系统层强制门禁，即使 Agent 绕过自身工具
   包装也无法篡改状态（K8s 下 sidecar 独占挂载 `.agent_gate`）。

## 通用接入点（任何框架）

```python
from anti_shortcut.proxy_client import GateClient

gate = GateClient("http://localhost:8080")     # sidecar 地址
gate.write_file("spec.md", spec_text)          # 跳步抛 GateDenied
gate.execute_command("python -m pytest -q")    # 测试命令自动记录结果
gate.advance(2)                                # 申请进入下一阶段
```

- `GateClient` 仅依赖标准库 `urllib`，可放进任意框架的工具实现。
- 被拦截统一抛 `anti_shortcut.proxy_client.GateDenied`；命令行等价物
  `python -m anti_shortcut write|exec|advance`（见 CLI 章节）。

## LangChain

- 示例：`examples/langchain_integration/`
- 思路：把 `GateClient` 包装为 `Tool.from_function` 的函数，返回 JSON 字符串；
  跳步时返回 `{"ok": false, "denied": "..."}`，LLM 读取后补写证据。
- 代码：

```python
from langchain.tools import Tool
from examples.langchain_integration.gate_tools import make_tools

tools = make_tools(gate)
tool = Tool.from_function(func=tools["write_file"]["func"], name="gate_write_file",
                          description=tools["write_file"]["description"])
```

- BaseTool 子类（v0.32.0）：`examples/langchain_integration/phase_barrier_tool.py`
  提供 `PhaseBarrierWriteTool` / `PhaseBarrierExecTool`（继承 `langchain_core.tools.BaseTool`），
  被拦截时同样返回 denied JSON；最小演示 `python examples/langchain_integration/demo.py`
  （安装 langchain-core 走真实 BaseTool 路径，否则自动回退 gate_tools 函数路径），
  CI 的 `integration-langchain` job 固定安装 `langchain-core>=0.3,<0.5` 验证。

## AutoGPT

- 示例：`examples/autogpt_integration/`
- 思路：包装命令注册表中的 `write_file` / `execute_shell`，被拦截返回
  `GATE_DENIED: <原因>`，Agent 调整计划后重试。
- 代码：

```python
from examples.autogpt_integration.gate_command_wrapper import install
wrapped_commands = install(gate, my_command_registry)
```

## SWE-agent

- 示例：`examples/swe_agent_integration/`
- 思路：把 `gate_tool.py`（零依赖 CLI）注册为 SWE-agent 工具，写文件 / 执行命令
  经 `/api/write` / `/api/exec` 代理；`PB_SIDECAR_URL` 环境变量指定 sidecar 地址。
- 工具配置模板：`examples/swe_agent_integration/swe_agent_example.yaml`

## Alpha-SWE（本仓库关联项目）

- 编排器钩子 SDK（v0.22.0）已接入 alpha-swe：任务启动 `barrier.check(stage)`、
  阶段切换 `barrier.advance(to_stage)`（[alpha-swe#3](https://github.com/Xuqing0415/alpha-swe/pull/3)）。

### 上游状态跟踪（v0.48.0）

- 记录位置：本页即权威跟踪记录；合并 / 关闭后在此更新状态，并在
  CHANGELOG 中补一条说明。
- 查询命令：`gh pr view 3 -R Xuqing0415/alpha-swe`（状态为外部仓库属性，
  不受本仓库发布节奏控制）。
- 当前状态（更新于 v0.48.0 之后）：**merged（上游已合入 master）**。
- 合并信息：2026-08-30 由 Xuqing0415 合入，合并提交 `128e6a4`（PR #3，`feat/phase-barrier-gate`）；
  quality-gate（flake8 / pytest / docker build）与 chaos-stage 检查全部 SUCCESS。
- 后续动作：本仓库 `docs/orchestrator-hooks.md` 与示例保持独立演进；若上游新版本
  行为有变化，在此更新状态并在 CHANGELOG 补一条说明。

## K8s sidecar 部署

```bash
# 一键体验（kind 本地集群）
bash deploy/k8s/kind-e2e-test.sh

# Helm 生产部署
helm install phase-barrier ./deploy/helm/phase-barrier --set persistence.type=pvc ...
```

- Agent 容器与 sidecar 共享工作区卷；`.agent_gate` 门禁状态卷由 sidecar 独占挂载，
  Agent 无法直接读写（详见 `deploy/helm/phase-barrier/README.md`）。
- mTLS：`GateClient(base_url, cert=(crt, key), ca=...)`（见 `examples/mtls_sidecar/`）。
