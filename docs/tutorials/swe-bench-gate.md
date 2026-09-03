# 教程：SWE-bench 门禁基准

[SWE-bench](https://www.swebench.com/) 用真实 GitHub issue 评估编码 Agent 的修 bug 能力。
phase-barrier 可以在评估流水线中作为 **过程合规门禁**：Agent 必须按
`需求 -> spec -> 测试 -> 实现 -> 测试 -> 修复 -> 交付` 推进，任何跳步都会被拦截并记录，
从而量化“Agent 是否按工程师 SOP 干活”，而不只是“最终对不对”。

## 一、为什么需要过程门禁

传统 SWE-bench 只打分“是否通过隐藏测试（resolve / fail-to-pass / pass-to-pass）”，
过程是黑盒：Agent 可能直接抄代码、跳过测试、没有需求分析也能拿分。
phase-barrier 门禁基准补充三个维度：

1. **SOP 合规率**：多少任务按阶段顺序推进到交付（阶段 6）。
2. **跳步拦截率**：多少跳步尝试被门禁拦截（`check` 返回 denied / 工具抛 `GateDenied`）。
3. **证据完整性**：spec / 测试 / 实现是否都真实产出且通过校验（非空壳）。

## 二、基准设计

对每个 SWE-bench 任务（repo + base_commit + issue）：

1. 初始化门禁状态，写入需求原文（阶段 0）。
2. 用带门禁的 Agent 跑任务（SDK 或 sidecar 透明代理）。
3. 记录每步 `check` / `advance` / `write` / `exec` 结果与拦截事件。
4. 任务结束后汇总指标。

```python
from anti_shortcut import AntiShortcutSkill

def run_with_gate(task, agent, workspace):
    skill = AntiShortcutSkill(
        workspace=workspace,
        config="config.yaml",
        user_request=task["problem_statement"],   # 阶段 0 证据
    )
    tools = skill.install(agent.tools)             # 包装 write_file / execute_command
    agent.tools = tools
    agent.run(task, workspace)                     # 正常 Agent 循环

    barrier = skill.sdk                                # PhaseBarrier
    state = barrier.inspect()
    return {
        "instance_id": task["instance_id"],
        "final_stage": state["stage"],
        "resolved": run_hidden_tests(task, workspace),  # SWE-bench 原始打分
        "sop_compliant": state["stage"] >= 6,
        "interceptions": len(skill.audit.interceptions()),  # 拦截事件数
    }
```

## 三、指标定义

| 指标 | 定义 | 意义 |
|------|------|------|
| Resolve rate | 通过隐藏测试的任务比例 | SWE-bench 原始指标 |
| SOP 合规率 | 最终阶段 ≥ 6 的任务比例 | Agent 是否走完流程 |
| 跳步拦截率 | 拦截事件数 / 任务数 | 门禁是否真的挡住跳步 |
| 证据完整性 | 通过证据校验的任务比例 | 产出是否非空壳 |
| 阶段停留时间 | 各阶段平均耗时 | 定位卡点（如测试写太慢） |

建议对比三组：**无门禁 / 只审计不拦截 / 强制拦截**，区分“能力问题”与“流程问题”。

## 四、集成方式

### 方式 A：SDK（推荐，最小侵入）

适用于你能改 Agent 工具注册表的评估框架：

```python
from anti_shortcut.sdk import PhaseBarrier

barrier = PhaseBarrier(workspace=workspace, config="config.yaml")
ok, msg = barrier.check(2)          # 编排器钩子：能否进入阶段 2
barrier.advance(2)                  # 证据校验通过后推进
barrier.record_test_run({"command": "pytest -q", "exit_code": 0, ...})
barrier.verify_evidence(git_base="origin/main")  # 增量校验
```

示例见 `examples/orchestrator_hooks/`（含编排器与多 Agent 协作演示）。

### 方式 B：sidecar 透明代理

评估 Agent 跑在容器里，文件写入/命令执行走 `GateClient`（`/api/write`、`/api/exec`），
Agent 无法绕过（K8s sidecar 模式，见 [K8s 教程](k8s-sidecar-grpc.md)）。
`GateDenied` 异常即拦截事件，可计入指标。

### 方式 C：CLI 兜底

```bash
python -m anti_shortcut inspect --workspace . --json     # 读取最终阶段
python -m anti_shortcut verify-evidence --git-base origin/main --workspace .
```

适合事后审计或批量跑完再校验。

## 五、注意事项

- **不要泄露 golden patch**：隐藏测试只用于最终打分，不能作为阶段 2 的测试证据；
  阶段 2 要求 Agent **自己写测试**。
- **门禁管过程、不管正确性**：合规不等于正确，两者要分开报告（上表指标拆开）。
- **超时与卡死**：门禁会拦截非法操作，评估循环要处理 `GateDenied` / `PermissionError`，
  避免 Agent 卡在重试死循环。
- **多语言任务**：按仓库标志文件自动检测（`detect_language`），显式 `language` 配置更稳。
- **成本**：强制 SOP 会增加 token/时间开销，用“跳步拦截率”证明其价值，而不是只看 resolve rate。

## 六、产出示例

```json
{
  "n_tasks": 100,
  "resolve_rate": 0.31,
  "sop_compliance_rate": 0.87,
  "shortcut_interception_rate": 0.94,
  "evidence_integrity_rate": 0.96,
  "avg_interceptions_per_task": 2.3
}
```

如果 `resolve_rate` 相当但 `sop_compliance_rate` 明显更高，说明门禁没有损失能力；
如果 `shortcut_interception_rate` 高，说明 Agent 确实有跳步倾向，门禁在起作用。


## 七、脚本化基准（v0.30.0）

仓库内置可重复运行的 SWE-bench 门禁基准脚本 `benchmarks/swe_bench_gate.py`，无需真实
SWE-bench 数据集即可验证门禁的拦截与推进行为：

```bash
python benchmarks/swe_bench_gate.py                      # 默认 20 个模拟任务
python benchmarks/swe_bench_gate.py --tasks 50 --json    # JSON 输出
python benchmarks/swe_bench_gate.py --fail-fast          # CI 阈值门禁
```

脚本用 `AntiShortcutSkill` + 包装工具（write_file / execute_command / advance_stage）驱动每个
模拟任务，覆盖三种行为路径：

1. **按 SOP 推进**：写 spec -> advance 2 -> 写测试 -> advance 3 -> 写实现 -> advance 4 ->
   运行 pytest -> advance 5 -> 自动进入交付 6。
2. **跳步**：阶段 1 直接写实现（`write_file` 拦截）与 shell 重定向写实现（`execute_command`
   拦截）各记 1 次拦截；拦截后可按配置选择放弃或回退按 SOP 完成。
3. **空壳测试**：先写空测试文件，`advance_stage(3)` 被证据校验（AST 检查测试函数与断言）
   拒绝，记录 `evidence_failures` 后补写真实测试继续推进。

聚合指标除上文"三、指标定义"外，另有：

| 指标 | 说明 |
|------|------|
| `sop_compliance_rate` | 最终阶段 ≥ 6 的任务占比 |
| `shortcut_interception_rate` | 跳步尝试被拦截的比例（期望 1.0） |
| `evidence_fix_rate` | 证据校验失败后被修复并完成的任务占比 |
| `resolve_rate` | 模拟隐藏测试通过率（`resolve_sop_rate` / `resolve_shortcut_rate` 抽样） |
| `avg_interceptions_per_task` | 平均每任务拦截次数 |

CI（`.github/workflows/ci.yml` 的 `bench` job）已纳入：

```bash
python benchmarks/swe_bench_gate.py --fail-fast --tasks 20
```

阈值：合规率 ≥80%、拦截率 ≥90%、单任务拦截 ≤5 次，超阈值即失败，防止门禁回归。
