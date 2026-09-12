# 学习型防线（路径 4，v0.59.0 / v0.60.0 半自动闭环）

> 学习型防线不是「用机器学习替代规则」，而是**从拦截 / 逃逸案例中自动提取候选模式，
> 由人审核后更新规则库与提示词**。

三个阶段：**采集 → 提取 → 更新**。核心安全约束是：**自动只到「建议」为止，合入必须有人**。

## 组成

| 文件 | 作用 |
|------|------|
| `anti_shortcut/defense/case_library.py` | 案例库读写、失败 / 逃逸采集钩子、触发类型分类 |
| `anti_shortcut/defense/case_analysis.py` | 分组分析、模式提取、规则合入（含反查与幂等）、提示词 few-shot |
| `scripts/analyze_cases.py` | CLI：案例库 → 分析报告 + 更新建议 |
| `scripts/update_rules.py` | CLI：建议 → （人工审核后）合入规则文件 |
| `anti_shortcut/defense/learned_rules.yaml` | 学习产物（防线 4 会加载其中的 `forbidden_patterns`） |
| `.github/workflows/learning-loop.yml` | 半自动闭环：采集 → 分析 → PR（含复测）/ issue（复测失败） |
| `scripts/update_rules.py --promote` | 置信度管理：`observation` → `active`（按复测确认次数） |

## 采集

案例库是 JSONL（每行一条），默认**按工作区**落在
`<workspace>/.agent_gate/defense/case_library.jsonl`；

```yaml
defense:
  case_library:
    enabled: true                 # 默认关闭
    library_file: case_library.jsonl
    shared_library: null          # 跨工作区汇总库（同一机器 / 同一 CI 共用）
```

两个采集点：

- **被拦下的案例**：`run_defense_checks()` 里任一防线失败即调用 `capture_defense_failure()`
  （含防线名、触发类型、拦截理由、阶段迁移、需求摘要、违规片段）；
- **逃逸成功的案例**：红队 Agent 判定 `escaped` 时调用 `capture_escape()`，把**攻击载荷**
  写进案例库（危险命令排在载荷最前，避免被 800 字上限截掉）。

触发类型由 `classify_trigger()` 归类：`untraced_write` / `forbidden_op:<类别>` /
`missing_test_command` / `vague_template` / `template_missing` / `static_contradiction` /
`alias_contradiction` / `semantic_unavailable` / `semantic_tamper` / `risk_sampling` /
`escape:<技术>` 等。

> 红线：**采集绝不影响门禁判定** —— 写失败、解析失败、配置异常全部被吞掉，只记日志。

## 提取

```bash
python scripts/analyze_cases.py --cases <案例库或目录> \
  --report analysis_report.json --markdown analysis_report.md --suggestions suggestions.json
python scripts/analyze_cases.py --workspace . --json     # 读 <workspace>/.agent_gate/defense
```

按 `(防线, 触发类型)` 分组后提取：

- **防线 4**：从案例载荷里抽「动词 + 选项」（`truncate -s`、`find -delete`）与点号 API
  （`os.truncate`），再与 `BUILTIN_FORBIDDEN` 做减法，只留规则库**尚未覆盖**的；
  文件名 token（`notes.txt`）与子串误命中（`truncate` 里的 `nc`）会被过滤；
- **防线 1**：从拦截理由里抽空话短语；
- **防线 3**：抽冲突变量名；
- **防线 2B**：抽弱化词（已在防线 2 提示词里明确的词不重复建议）。

逃逸案例若没声明 `attack_category`，**不猜类别**，只记录「需人工判断」，避免生成风马牛
不相及的规则。

## 更新（人工审核后）

```bash
# dry-run：只做校验与统计，不落盘
python scripts/update_rules.py --suggestions suggestions.json \
  --dest anti_shortcut/defense/learned_rules.yaml --verify-cases case_library.jsonl

# 人工审核差异后落盘
python scripts/update_rules.py --suggestions suggestions.json \
  --dest anti_shortcut/defense/learned_rules.yaml --verify-cases case_library.jsonl --apply
```

`:--apply` 的三道闸门：

1. **正则编译校验**：非法正则丢弃；
2. **反查**：建议的正则必须至少命中一条它来自的案例原文，否则丢弃
   （`--apply` 时不给 `--verify-cases` 会直接拒绝执行，除非显式 `--allow-unverified`）；
3. **幂等**：已在目标文件里的条目不会重复写入。

规则文件里的 `forbidden_patterns` 由防线 4 直接加载（`defense.behavior_audit.
extra_forbidden_patterns_file`）；`vague_phrases` / `weakening_words` / `few_shot_examples`
是给防线 1 配置与防线 2 提示词的人工评审输入，**不会被代码自动应用**：

```bash
python scripts/update_rules.py --suggestions suggestions.json --dest learned_rules.yaml \
  --verify-cases case_library.jsonl --apply \
  --prompt anti_shortcut/defense/prompts/tamper_check.txt   # 标记块整体重写，多次运行幂等
```

**fail-closed**：配了 `extra_forbidden_patterns_file` 但文件缺失或解析失败时，防线 4 直接
拒绝交付 —— 规则文件写坏 ≠ 没有规则。

### 规则文件结构（v0.60.0：auto / manual 分区块）

v0.59.0 的一个真实缺陷：`--apply` 会用建议里的 `note` 覆盖人工写的 `note`（人工审核
痕迹被抹掉）。v0.60.0 起规则文件按「机器区 / 人工区」分开：

```yaml
schema_version: 2
note: "人工说明（人工区：--apply 永不覆盖）"
auto:                       # 机器区：每次 --apply 刷新
  generated_by: analyze_cases.py
  updated_at: "2026-09-11T00:00:00Z"
  suggested_note: "本文件由案例库分析生成……"
rules:                      # 逐条规则：auto 机器更新 / manual 人工审核痕迹
  - rule_id: rule-4925aa5d1e5d
    category: file_delete
    pattern: '(?i)\btruncate\s+[^\n;&|]{0,40}?\-s\b'
    auto:
      confidence: active            # observation -> active（复测确认后升级）
      confirmations: 3
      source_cases: [case-71c21fa316bd]
      updated_at: "2026-09-11T00:00:00Z"
    manual:
      note: "人工确认：truncate -s 0 属于 file_delete（清空文件）"
      approved_by: Xuqing0415
      approved_at: "2026-09-11"
forbidden_patterns:         # 兼容视图：由 rules 汇总，防线 4 直接读它
  file_delete:
    - '(?i)\btruncate\s+[^\n;&|]{0,40}?\-s\b'
```

`scripts/update_rules.py --apply` 只刷新 `auto` 与顶层 `auto:` 区块；顶层 `note` 与
`rules[].manual` 原样保留（有回归测试 `test_apply_suggestions_preserves_manual_note_block`）。

### 规则置信度

新规则以 `confidence: observation` 进入规则库（**照常参与拦截**，只是标记「尚未反复
验证」）。每通过一轮红队全量复测就 `+1`，累计到门槛后升级：

```bash
python scripts/update_rules.py --dest anti_shortcut/defense/learned_rules.yaml \
  --promote --confirmations-required 3 --apply
```

### 半自动闭环（v0.60.0）

`.github/workflows/learning-loop.yml`（每周一 04:23 UTC + 手动）把闭环从「人推动」
变成「机制推动」，但**合入仍由人工在 PR 上完成**：

```text
红队全量跑（采集案例） -> analyze_cases.py -> update_rules --apply（先 dry-run）
   -> 复测（加载候选规则再跑红队） -> 复测通过则开 PR / 不通过则开 issue
   -> 人工审核合并 -> --promote 累积确认次数
```

PR 正文自动包含：规则 diff、来源案例与提取报告、复测结果、人工审核清单。复测失败
（仍有真实漏洞）不会提 PR，而是建 issue —— 说明候选规则不够，需要人判断。

## 与红队的联动（闭环）

```text
红队攻击 → 逃逸案例 → 模式提取 → 人工审核合入 → 红队复测 → 确认堵住
```

红队沙箱默认加载 `learned_rules.yaml`（`SandboxConfig.use_learned_rules=True`），
即「用**当前**防御姿态跑红队」；`--no-learned-rules` 用于复现「学到之前」。

## 闭环实证（2026-09-11）

产物在 `benchmarks/red_team/results/learning_loop/`：

| 步骤 | 命令 | 结果 |
|------|------|------|
| 1. 修复前跑红队 | `run_red_team.py --no-learned-rules --case-library .../case_library.jsonl` | 15 个案例：拦截 11 / 不可判定 3 / **逃逸 1**（`equivalent_op/truncate_zero`） |
| 2. 分析案例库 | `analyze_cases.py --cases case_library.jsonl ...` | 20 条案例分 8 组，逃逸组提取出 **1 条**新规则候选 |
| 3. 审核合入 | `update_rules.py ... ` 先 dry-run 再 `--apply` | 反查命中后写入 `learned_rules.yaml`（`file_delete` 类别） |
| 4. 修复后复测 | `run_red_team.py --fail-on-vulnerability` | **真实漏洞 0 个**；`truncate_zero` 被防线 4 用学到的规则拦下（阶段 4） |

对照可见于 `before/report.json`（`escaped`）与 `after/report.json`（`blocked`，拦下理由里带
的就是学到的正则）。

## 持续运转与告警（v1.0.0）

自动化闭环最大的风险是「配了但没跑」——job 全绿、其实几周没有产出。为此：

- **运行摘要**：每次运行把「触发方式 / 采集案例数 / 复测结果 / 候选规则状态 / run 链接」
  写进 job summary，一眼能看出这一步是否真的执行了。
- **产出健康检查**（`output-healthcheck` job）：每次运行时查最近一条带 `learning-loop`
  标签的 PR/issue，若已连续 14 天没有产出就自动开 issue 提醒（按标题前缀去重，不会重复刷）。
  该检查同时覆盖「没跑」与「跑了没产出」两种情况。

## 局限

- 提取是**启发式**的（从文本抽正则），只负责把候选摆到人面前；宽严由人工审核把关；
- 只能补**规则可表达**的缺口。设计级缺口需要改机制而不是加正则：风险伪装的
  「第一个子任务必然没有历史」已由 v0.60.0 的**需求族风险累积**（`task_family_id` +
  `task_family_detection`）补上；仍然存在的残余情形见
  [red-team.md](red-team.md) 的残余风险表。
- 案例库按 800 字截断载荷，且**不做**自动合入 —— 这是刻意的：自动更新规则可能引入误报
  或恶意规则，所以合入路径永远留一个人；
- 置信度升级（`--promote`）只统计「复测通过次数」，不等于「规则一定正确」；宽严仍由
  人工在 PR 上把关。
