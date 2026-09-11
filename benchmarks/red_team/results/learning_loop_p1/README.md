# 学习闭环复现（v0.60.0）

一次**真实跑通**的「红队逃逸 → 采集 → 分析 → 合入 → 复测」闭环，用于回归
`learning-loop.yml` 与 `update_rules.py` 的行为。目录内全部为真实命令产物。

| 步骤 | 命令（要点） | 产物 | 结果 |
|------|--------------|------|------|
| 1. 逃逸 | `run_red_team.py --techniques equivalent_op --no-learned-rules --case-library case_library.jsonl` | `case_library.jsonl` | 6 个案例：拦截 5 / **逃逸 1**（`equivalent_op/truncate_zero`） |
| 2. 分析 | `analyze_cases.py --cases case_library.jsonl ...` | `analysis_report.json/.md`、`suggestions.json` | 6 条案例 / 2 组 / **1 条新规则候选** |
| 3. 合入 | `update_rules.py --verify-cases case_library.jsonl --apply` | `apply.json` | 规则已存在 -> 走 `updated` 分支（**人工 note 保留**），`added` 为空（幂等） |
| 4. 复测 | 加载合入后的规则文件再跑 `equivalent_op` | `after.json` | **真实漏洞 0 个**，`truncate_zero` 被防线 4 拦下（阶段 4） |
| 5. 置信度 | `update_rules.py --promote --confirmations-required 2` | `promote.json` | 第 1 轮 `observation`（confirmations=1）→ 第 2 轮升级 `active` |

## P0 修复的实证（人工 note 不被覆盖）

第 3 步在已有规则的场景下 `apply_suggestions` 只刷新 `auto` 区块：

- 顶层 `note`（人工说明）保持原文；
- `rules[0].manual.note`（"人工确认：truncate -s 0 属于 file_delete…"）保持原文；
- `rules[0].auto.updated_at` 被刷新，`confidence` / `source_cases` 并入。

对应回归测试：`tests/test_learning_defense.py::test_apply_suggestions_preserves_manual_note_block`。

## 复测证据（after.json）

```json
{"counts": {"blocked": 6}, "vulnerabilities": []}
```

六个 `equivalent_op` 变体全部被防线 4 拦下，其中 `truncate_zero` 命中的正是
`learned_rules.yaml` 里学到的正则。
