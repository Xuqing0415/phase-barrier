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

## 三种接入形态（v0.27.0 起齐全，2026-09 按真实代码复核）

先选形态，再选框架。三种形态的操作面一致：`write_file` / `execute_command` /
`advance_stage`，被拒时都要「补齐证据后重试」。

### 形态 A：进程内包装（最快，同信任域）

```python
from anti_shortcut import bootstrap

tools = {
    "write_file": my_write_file,        # def write_file(path: str, content: str) -> object
    "execute_command": my_exec,         # def execute_command(command: str, cwd: str = None) -> object
}

# bootstrap(agent_tools, workspace, config=..., user_request=...) -> AntiShortcutSkill
skill = bootstrap(
    tools,                                  # 工具字典会被原地替换为经门禁包装的版本
    "/path/to/project",
    config="anti_shortcut_config.yaml",     # YAML 路径 / dict / GateConfig / None
    user_request="实现登录功能，禁止明文存储密码……",   # 阶段 0 证据
)
tools["advance_stage"]                      # 由 bootstrap 注入，指向 skill.advance_stage
```

- 第一个参数是**工具字典**，不是 agent 对象，也没有 `register_tool` 回调。
- 校验失败时包装器抛 `PermissionError`，原因写在异常消息里。
- 参考实现：`examples/minimal_agent.py`（下面「验证集成」有它的真实输出）。

### 形态 B：sidecar（推荐，进程隔离）

```bash
python -m anti_shortcut sidecar --workspace /workspace --config anti_shortcut_config.yaml --port 8765
```

```python
from anti_shortcut.proxy_client import GateClient, GateDenied

gate = GateClient("http://127.0.0.1:8765")
gate.write_file("spec.md", spec_text)          # 被拒抛 GateDenied
gate.execute_command("python -m pytest -q")    # 命令输出会被解析并自动记录测试结果
gate.advance(2)                                # 证据不足时抛 GateDenied

try:
    gate.write_file("impl.py", code)
except GateDenied as exc:
    print(exc.status, exc.reason)              # 403 + 可读原因
```

- 客户端模块是 **`anti_shortcut.proxy_client`**；`GateClient(base_url, timeout=30.0,
  cert=(crt, key), ca=...)`，`cert`/`ca` 用于 mTLS。
- 生产部署把 `.agent_gate/` 只读挂给 Agent 容器（写权限在 sidecar 侧）：
  `deploy/helm/phase-barrier/`。

### 形态 C：CLI 包装（零代码改造）

```bash
python -m anti_shortcut write   --workspace /workspace --path src/main.py --stdin
python -m anti_shortcut exec    --workspace /workspace --command "python -m pytest -q"
python -m anti_shortcut advance --workspace /workspace --to 2
```

### 常见错误写法（照着抄会立刻报错，均已实测）

| 错误写法 | 实际 API | 实测现象 |
|---|---|---|
| `from anti_shortcut.gate_client import GateClient` | `from anti_shortcut.proxy_client import GateClient` | `ModuleNotFoundError: No module named 'anti_shortcut.gate_client'` |
| `bootstrap(workspace=..., config_path=...)` | `bootstrap(agent_tools, workspace, config=...)`（无 `config_path`） | `TypeError: bootstrap() missing 1 required positional argument: 'agent_tools'` |
| `gate.write(...)` / `gate.exec(...)` | `gate.write_file(...)` / `gate.execute_command(...)` | `AttributeError: 'GateClient' object has no attribute 'exec'` |
| `if result.blocked: ...` | `try/except GateDenied`（`exc.reason`） | `AttributeError: 'GateClient' object has no attribute 'blocked'` |

### 验证集成是否生效（真实输出，2026-09-12 复跑）

```bash
python examples/minimal_agent.py
```

阶段 1 直接写实现 / 跑测试会被拦，按 SOP 补齐 spec -> 测试 -> 实现 -> 测试后一路推进：

```text
=== 阶段 1（Spec 设计）===
[BLOCKED] write_file(fib.py, ...)  -> 当前阶段不允许编写实现代码：请先完成测试用例编写（阶段 2）
[BLOCKED] execute_command(pytest -q) -> 当前阶段不允许运行测试命令：请先完成实现代码（阶段 3）
=== 按 SOP 推进 ===
[OK] write_file(spec.md, ...)     [advance] -> 已进入阶段 2（测试用例编写）：spec 校验通过
[OK] write_file(test_fib.py, ...) [advance] -> 已进入阶段 3（实现代码）：测试用例校验通过（1 个文件，2 个测试函数）
[OK] write_file(fib.py, ...)      [advance] -> 已进入阶段 4（运行测试）：实现代码校验通过（1 个文件，语法检查 OK）
[OK] execute_command(pytest -q)   [advance] -> 已进入阶段 6（交付）：测试全部通过，跳过修复阶段，直接进入交付
最终阶段: 6（交付），完成 = True
```

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
