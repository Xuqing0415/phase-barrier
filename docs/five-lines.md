# 五道防线（v0.52.0）

> 核心原则：不追求单点完美，追求多层防线叠加后的**逃逸成本最大化**。
> 语义校验解决“文档有没有内容”，五道防线进一步解决“内容有没有偷换约束”——
> 拦截 Agent 在 spec / 实现中擅自弱化、删除、替换关键限制条件的绕过方式。

```scss
需求进入 → 防线1(需求模板) → 阶段1 Spec → 防线2(双模型交叉复核) → 防线3(形式化校验)
         → 阶段2 测试 → 阶段3 实现 → 阶段4 测试运行 → 防线4(运行时行为审计)
         → 阶段6 交付前 → 防线5(概率人工复核)
```

## 配置总览

全部防线**默认关闭**（与 v0.49.0 语义层一致，不影响既有门禁行为）。在
`config.yaml` 的 `defense:` 节启用后，防线叠加于既有结构 / 语义校验之上；
任一防线失败即阻止阶段推进。配置字段说明见
[配置指南](configuration.md) 与各节。

```yaml
defense:
  requirement_template:   # 防线 1（阶段 1 -> 2）
    enabled: false
    strict: false         # true：自由文本需求（无模板）直接拒绝
    template_file: requirement.yaml
  dual_review:            # 防线 2（阶段 1 -> 2）
    enabled: false
    fail_closed: true
    min_confidence: 0.0
    reviewer_a: { endpoint: ..., model: ..., api_key_env: OPENAI_API_KEY }
    reviewer_b: { endpoint: ..., model: ..., api_key_env: DEEPSEEK_API_KEY }
  formal_check:           # 防线 3（阶段 1 -> 2）
    enabled: false
    constraints_file: constraints.yaml
    require_tlc: false
  behavior_audit:         # 防线 4（阶段 4/5 -> 6 交付前）
    enabled: false
    trace_file: trace.jsonl
    deny_missing_test_command: false
    scan_deliverables: true  # 交付物源码兜底扫描
    scan_chains: true        # 工具链补扫：trace 写过的路径按磁盘完整内容再扫（v0.60.0）
  human_review:           # 防线 5（阶段 4/5 -> 6 交付前）
    enabled: false
    sample_rate: 0.3
    force_above_score: 80
    auto_approve_below_score: 20
    rollup_ledger: null          # 跨会话风险账本（相对工作区或绝对路径）
    task_family_detection: false # 需求族聚合（v0.60.0）
    task_family_window_hours: 24
    task_family_min_tasks: 3
    task_family_score_threshold: 60
```

## 防线 1：需求模板（前置约束）

自由文本需求是逃逸源头。`anti_shortcut init-requirement` 生成模板，四个必填区块：

| 区块 | 要求 |
| --- | --- |
| `goal` | 明确目标，默认 ≤ 200 字 |
| `forbidden` | 逐条枚举不允许的行为（默认 ≥ 1 条） |
| `interfaces` | 具体输入输出签名（默认 ≥ 2 条） |
| `acceptance` | 可客观判断的通过条件（默认 ≥ 2 条） |

```bash
python -m anti_shortcut init-requirement --workspace . --example      # 骨架
python -m anti_shortcut init-requirement \
  --goal "登录鉴权，凭据正确放行；连续 5 次失败锁定 30 分钟" \
  --forbidden "不允许明文存储密码;不允许修改数据库表结构" \
  --interfaces "def login(user, pwd) -> bool;def lock_status(user) -> LockState" \
  --acceptance "正确凭据返回 True;连续 5 次错误则锁定"
```

`requirement.yaml`（或 `requirement.json`）保存在工作区根；校验通过后模板写入
状态机证据，作为防线 2 / 3 / 4 的统一比对基准。

### 验收对照

- 仅写“实现登录功能”的自由文本需求：`strict: true` 下无法通过首个推进点，错误
  消息列出缺失区块；
- 按模板填写的需求正常放行（状态证据出现 `requirement_template`）。

## 防线 2：双向语义校验（双模型交叉复核）

单一 LLM 校验器可能被话术说服。防线 2 部署两个**互相独立**的实例：

- 实例 A（正向覆盖核查）：需求的每条目标 / 禁止行为 / 接口 / 验收在 spec 中是
  否有对应设计（`covered / partially_covered / missing`）；
- 实例 B（反向篡改核查）：spec 是否存在对需求的
  `added_constraint`（新增约束）/ `removed_constraint`（删减 / 弱化）/
  `replaced_constraint`（替换语义）三类篡改。

两个实例应使用**不同模型 / 服务商**（OpenAI / DeepSeek / Anthropic / 本地模型均可，
端点需兼容 OpenAI chat 协议），提示词完全独立（内置
`anti_shortcut/defense/prompts/{coverage_check,tamper_check}.txt`，可经
`prompt_file` 覆盖）。两侧**同时 pass** 才放行；调用失败默认拒绝（`fail_closed`）；
置信度低于 `min_confidence` 视为不可靠。

结论落盘 `.agent_gate/defense/dual_review.json`（双份原文 + 解析结果）。

### 验收对照

- 已知篡改样本（需求“密码至少 8 位”，spec 写“至少 6 位”）由实例 B 拦截；
- 单侧 fail 即拒绝，错误信息具体到条目 / 篡改位置。

## 防线 3：形式化校验（约束 DSL + 可选 TLC）

语义校验对“逻辑矛盾”（如密码 ≥ 8 且 ≤ 6、声明 1000 并发却是单线程阻塞）无力；
形式化表达后可立即发现。`constraints.yaml` 约束 DSL：

```yaml
formal_constraints:
  - id: C1
    description: 密码长度至少 8 位
    type: range            # range / eq / neq / min_count / max_count
    variable: password_len
    operator: ">="
    value: 8
  - id: C2
    description: 同一用户并发会话不超过 3
    type: max_count
    variable: active_sessions
    value: 3
```

- **静态区间矛盾检测**（内置、无外部依赖）：同一变量下界 / 上界 / 等值求交为空
  即拒绝（无需 TLC）；
- **TLC 模型检查**（可选）：配置 `tlc_bin`（或 PATH 上有 `tlc`）时对生成的
  TLA+ 模块运行 `tlc -clean -terse`，检查语法与不变量；
- 高级用户可放置手写 `.tla` 模块（把 `constraints_file` 指向 `.tla`），
  `require_tlc: true` 时缺少 TLC 会拒绝放行；
- **未提供任何约束文件 → 降级为警告不拦截**（保持向后兼容）。

证据落盘 `.agent_gate/defense/tla_check.json`（含生成的 TLA+ 文本）。

### 验收对照

- “密码至少 8 位” + “密码至多 6 位”矛盾：静态检测拦截；
- 无 `.tla` / 无约束：降级警告，不改变既有行为。

## 防线 4：运行时行为审计（trace 比对）

静态门禁拦不住运行阶段的实际偏离。启用后 Skill 包装的
`write_file` / `execute_command` 会把脱敏后的工具调用写入
`.agent_gate/defense/trace.jsonl`（Agent 无法写入该目录，证据不可篡改）。
交付前与 spec / 需求模板声明的禁止操作比对：

- 机器可自动判定的内置类别（spec / 模板中出现“禁止 / 不允许 / 不得”关键词即启用）：
  `db_schema`（ALTER / DROP TABLE、迁移）、`network`（urllib / requests /
  http(s) / curl）、`file_delete`（rm -rf / os.remove / del *.py）、
  `secret`（硬编码密钥）；
- 自定义类别：`defense.behavior_audit.extra_forbidden_patterns: {类别: [正则...]}`；
- 命中禁止操作 → **拒绝交付**，报告写入 `behavior_diff.json`；
- trace 中完全没有测试命令：默认警告，`deny_missing_test_command: true` 时拒绝；
- **去混淆归一化**（v0.60.0）：匹配前把引号拼接（`r''m`）、反斜杠转义（`r\m`）、
  零宽字符还原成字面；`base64 -d | sh` / `xxd -r | sh` 等混淆管道有显式模式；
- **工具链补扫**（`scan_chains`，v0.60.0）：trace 里的 `write_file` 内容会被截断到
  4000 字符，交付物扫描又只覆盖 `source_extensions` ——「写 `.tmp` / `.env` /
  `Makefile` 脚本再执行」的组合会落在两条扫描之间。补扫对 trace 里出现过的写入路径
  按**磁盘完整内容**再扫一遍，命中记为 `source: chain`（`write_then_run`）。

### 验收对照

- spec 声明“不修改数据库”，trace 出现 `ALTER TABLE` → 交付前拦截；
- 声称已运行测试但 trace 无测试命令 → 标记（可配置拒绝）。

## 防线 5：概率人工复核

自动化防线都有理论上限。交付前按**风险分数**（0-100）做确定性抽样
（默认按“需求 + 目标阶段”哈希播种，同任务可复现）：

| 因素 | 上限 | 说明 |
| --- | --- | --- |
| 需求复杂度 | 35 | 目标长度 + 禁止 / 验收 / 接口条目数 |
| 双模型置信度 | 20 | 置信度越低风险越高 |
| 形式化是否启用 | 15 | 未启用风险更高 |
| 审计偏离数 | 20 | 每处违规 +5 |
| 变更规模 | 10 | trace 中写入文件数 |

- 抽样概率 = `min(sample_rate, score/100*sample_rate)`；
- `score >= force_above_score` 必抽；`score < auto_approve_below_score` 自动放行；
- **需求族聚合**（`task_family_detection`，v0.60.0）：同一 `task_family_id` 在
  `task_family_window_hours` 内的任务数 >= `task_family_min_tasks` 且风险总分 >=
  `task_family_score_threshold` → **强制人工复核**（不看单任务分数）。用于识别
  「把高风险任务拆成多个低风险子任务」的会话级攻击；族 ID 由编排器声明，缺失时
  回落到需求文本哈希；
- 命中抽样 → 生成 `.agent_gate/defense/human_review_request.json`
  （需求 + spec 摘要 + 双模型结论 + 形式化结果 + 行为审计报告），**不放行**；
- 人工核对后批准，再重新推进即可：

```bash
python -m anti_shortcut review-approve --workspace . --request-id <ID> --reason "已核对"
```

## 证据位置与可信链

方案中的 `evidence/` 目录落地为 `.agent_gate/defense/`——与 `state.json` /
`evidence_manifest.json` 同级，位于 Skill 独占的门禁目录，Agent 侧工具无法写入，
保证“行为证据不可事后伪造”。

## 与既有语义层的关系

- 语义层（v0.49-0.51，`semantic:`）负责**内容质量**：spec 具体性、断言质量、
  实现-文档双向追踪；
- 五道防线（v0.52.0，`defense:`）负责**约束忠诚度**：需求结构化、约束篡改识别、
  逻辑矛盾、行为越界、人工兜底；
- 两者默认全部关闭、可独立启用，开启后叠加，互不替代。
