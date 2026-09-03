# LangChain 集成示例（v0.27.0）

把 phase-barrier 的 `GateClient` 包装成 LangChain `Tool`，让 LLM Agent 的
文件写入 / 命令执行都经过阶段门禁：跳步时返回拦截原因（JSON），LLM 读到后
自动补写 spec / 测试，而不是直接产出越权代码。

## 运行

```bash
python examples/langchain_integration/gate_tools.py
```

## 接入 AgentExecutor

```python
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain.tools import Tool
from langchain_openai import ChatOpenAI

from anti_shortcut.proxy_client import GateClient
from examples.langchain_integration.gate_tools import make_tools

gate = GateClient("http://sidecar:8080")          # sidecar 地址
tools = make_tools(gate)

langchain_tools = [
    Tool.from_function(
        func=tools["write_file"]["func"],
        name=tools["write_file"]["name"],
        description=tools["write_file"]["description"],
    ),
    Tool.from_function(
        func=tools["execute_command"]["func"],
        name=tools["execute_command"]["name"],
        description=tools["execute_command"]["description"],
    ),
]

agent = create_openai_functions_agent(ChatOpenAI(model="gpt-4o"), langchain_tools, prompt)
executor = AgentExecutor(agent=agent, tools=langchain_tools, verbose=True)
executor.invoke({"input": "实现一个斐波那契函数 fib(n) 并保证质量"})
```

要点：

- 工具返回 JSON 字符串（LangChain 约定），`denied` 字段携带拦截原因（如
  “当前阶段不允许编写实现代码，请先完成测试用例编写”）。
- `advance_stage` 建议由编排器钩子在工具链外调用（见 `examples/orchestrator_hooks/`），
  或在工具描述中引导 Agent 先完成证据再申请推进。
- 生产环境把 `GateClient` 指向 K8s sidecar Service（Helm chart 默认 8080 端口）。

## PhaseBarrierTool（BaseTool 子类）与 demo（v0.32.0）

`phase_barrier_tool.py` 把 `GateClient` 包装为 `langchain_core.tools.BaseTool` 子类：

- `PhaseBarrierWriteTool`：经门禁写文件（入参 `path` / `content`）
- `PhaseBarrierExecTool`：经门禁执行 shell 命令（入参 `command`）

被拦截时返回 `{"ok": false, "denied": "..."}`（JSON 字符串，LangChain 工具约定），
LLM 读取拦截原因后补写 spec / 测试再重试。langchain 为可选依赖：未安装
`langchain-core` 时模块可正常导入（`HAS_LANGCHAIN=False`），实例化会抛出
带安装提示的 `RuntimeError`。

```python
# pip install "langchain-core>=0.3"
from anti_shortcut.proxy_client import GateClient
from phase_barrier_tool import PhaseBarrierWriteTool, PhaseBarrierExecTool

gate = GateClient("http://sidecar:8080")
write_tool = PhaseBarrierWriteTool(gate=gate)
write_tool.invoke({"path": "fib.py", "content": "..."})   # 跳步 -> denied JSON
```

最小演示（跳步拦截 + 按 SOP 全通）：

```bash
python examples/langchain_integration/demo.py
```

- 已安装 `langchain-core`：走真实 BaseTool 路径（`PhaseBarrierTool.invoke`）。
- 未安装：自动回退到 `gate_tools` 工具函数路径，脚本仍可完整运行。
- CI（`.github/workflows/ci.yml` 的 `integration-langchain` job）安装固定范围
  `langchain-core>=0.3,<0.5` 后运行 `demo.py`，验证真实 BaseTool 代码路径。
