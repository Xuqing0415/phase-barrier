# 编排器钩子 SDK

> 迁移自 README 精简版主页；可运行示例见 `examples/orchestrator_hooks/`。[返回 README](https://github.com/Xuqing0415/phase-barrier#readme)

Alpha-SWE 等 Agent 平台可以“轻量 SDK”方式在 **任务启动 / 阶段切换** 钩子接入阶段门禁：
校验逻辑留在本包，编排器只做调用，返回结构稳定、JSON 可序列化。

```python
from anti_shortcut import PhaseBarrier

barrier = PhaseBarrier(workspace=project_dir, user_request=user_request)

# 任务启动钩子：Agent 声称从阶段 1（spec 设计）开始
gate = barrier.check(1)
if not gate["allowed"]:
    prompt = gate["message"]   # 回传给 Agent，强制补全前置证据

# 阶段切换钩子：Agent 声称完成阶段 1，申请进入阶段 2
result = barrier.advance(2)
if not result["success"]:
    prompt = result["error"]
```

- `check(stage)`：只读校验，返回 `{allowed, stage, stage_name, current_stage, message, violations}`。
- `advance(to_stage)`：与 `advance_stage` 同一套证据校验，返回 `{success, stage, stage_name, message/error, evidence}`。
- `record_test_run({exit_code, output})`：登记测试运行结果（阶段 4 推进校验依赖）。
- `verify_evidence()`：返回 `{ok, violations, signed}`，清单缺失 / 签名不匹配统一 `ok=False`。
- `list_stages()`：阶段清单，返回 `[{stage, name, entry, evidence}]`（v0.26.2）。
- `stage_of(path)`：把文件路径归类到对应阶段证据（spec->1 / test->2 / source->3 / other->None），
  与 `verify-evidence --git-base` 的 `git_impact` 分类一致（v0.26.2）。
- `get_required_evidence(stage)`：证据库存（只读，v0.45.0），返回完成该阶段所需证据项的现有文件 / 是否满足，
  结构 `[{stage, name, evidence, kind, files, satisfied}]`；仅判断存在性，完整门禁用 `check` / `advance`。
- `get_last_test_run()`：返回最近一次测试运行详情（deep copy），未运行过返回 `None`（v0.45.0）。
- `get_stage_history(stage=None)`：阶段完成历史，每条 `{stage, name, timestamp, evidence}`，可按阶段过滤（v0.45.0）。
- `has_uncommitted_changes()`：Git 未提交变更检查：有变更 `True` / 干净 `False` / 非仓库或 git 不可用 `None`（v0.45.0）。

CLI 等价调用：`python -m anti_shortcut check --workspace . --stage 2 --json`；
辅助查询可用 `python -m anti_shortcut query --required-evidence 3 --json` /
`--last-test-run` / `--stage-history` / `--has-uncommitted-changes`（v0.45.0）。
完整示例见 `examples/orchestrator_hooks/`。
