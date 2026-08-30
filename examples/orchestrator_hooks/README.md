# 编排器钩子集成示例（v0.22.0）

演示 Alpha-SWE 等 Agent 平台以“轻量 SDK”方式接入 phase-barrier：
在 **任务启动** / **阶段切换** 两个钩子处调用 `PhaseBarrier`，
校验 Agent 声称的阶段是否有满足的前置证据；不满足时返回约束消息，
由编排器回传给 Agent 强制补全 spec / test。

## 运行

```bash
python examples/orchestrator_hooks/demo.py
```

## 两个钩子

| 钩子 | 调用 | 返回 |
|------|------|------|
| 任务启动 | `barrier.check(agent_stage)` | `{allowed, stage, stage_name, current_stage, message, violations}` |
| 阶段切换 | `barrier.advance(to_stage)` | `{success, stage, stage_name, message/error, evidence}` |

- `check` 是只读校验，不修改状态；`allowed=False` 时把 `message` 喂回给 Agent。
- `record_test_run(result)` 登记一次测试运行结果（`{exit_code, output}`），阶段 4 推进校验依赖它。
- `advance` 与 Agent 内部 `advance_stage` 走同一套证据校验（spec 章节 / 测试 AST / 语法 / 测试运行结果）。
- 也可用 CLI 等价调用：`python -m anti_shortcut check --stage N --json`。

## 与工具级拦截的关系

- `PhaseBarrier`（钩子校验）：编排器在任务启动 / 阶段切换时调用，适合平台统一管控。
- `AntiShortcutSkill.install`（工具级拦截）：包装 `write_file` / `execute_command` 实时拦截，适合 Agent 内部约束。
- 两者可叠加：钩子保证“阶段切换有据可依”，工具包装保证“过程行为不越权”。
