# AutoGPT 集成示例（v0.27.0）

把 AutoGPT 命令注册表中的 `write_file` / `execute_shell` 替换为经 phase-barrier
门禁的版本：跳步的命令调用返回 `GATE_DENIED: <原因>`，Agent 读到后补写证据。

## 运行

```bash
python examples/autogpt_integration/gate_command_wrapper.py
```

## 接入 AutoGPT

在 AutoGPT 初始化命令注册表后调用 `install`：

```python
from anti_shortcut.proxy_client import GateClient
from examples.autogpt_integration.gate_command_wrapper import install

gate = GateClient("http://sidecar:8080")
wrapped_commands = install(gate, my_command_registry)
# 把 wrapped_commands 作为 Agent 实际使用的命令表
```

说明：

- 被包装命令名：`execute_shell` / `write_file` / `read_file` / `file_operations`；
  `write` 走 `/api/write`，`exec` 走 `/api/exec`（测试命令自动记录结果），其余只读命令原样透传。
- `install` 不修改原命令表，返回新表，便于回滚。
- 被拦截时返回以 `GATE_DENIED:` 开头的字符串，方便 AutoGPT 的响应解析识别并调整计划。