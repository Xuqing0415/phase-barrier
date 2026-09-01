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