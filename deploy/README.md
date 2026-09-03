# Docker 只读卷部署示例

演示“不可绕过”的进程级隔离：即使 Agent 绕过工具包装，也无法篡改 `.agent_gate` 状态。

## 拓扑

```
gate-keeper 服务                         agent 服务（编码 Agent）
  /workspace          : 可写卷             /workspace            : 可写卷
  /workspace/.agent_gate : 可写挂载          /workspace/.agent_gate : 只读挂载（:ro）
```

- `gate-keeper`：对 `.agent_gate` **可写**，负责初始化状态并执行完整门禁流程
  （spec -> 测试 -> 实现 -> 测试 -> 交付）。
- `agent`：工作区可写以产出代码，但 `.agent_gate` **只读挂载**——即使 Agent 拿到了
  未包装的 `write_file` / `execute_command`，文件系统层面也不允许写入。

## 运行

```bash
docker compose -f deploy/docker-compose.yml up --build
```

预期输出（agent 服务）：

```
[probe] read state OK: current_stage = 6
[probe] OK: 写入门禁目录被拒绝（PermissionError）
[probe] OK: 工作区源码写入正常
```

## 说明

- 宿主目录：`deploy/workspace/`（代码工作区）、`deploy/gate_data/`（门禁状态数据）。
- 状态文件路径：`/workspace/.agent_gate/state.json`（即宿主 `deploy/gate_data/state.json`）。
- 本示例中的 `seed_gate.py` 直接复用了 Skill 的 `advance_stage` 门禁校验；
  真实系统中 gate-keeper 通常是编排器/监督进程，负责在 Agent 会话间推进状态。
