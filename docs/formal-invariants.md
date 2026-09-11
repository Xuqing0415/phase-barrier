# 形式化不变量（路径 2，v0.58.0）

> 测试能覆盖「我想到的路径」，模型检查覆盖**所有**路径。这一层的作用是把状态机的核心
> 保证写成机器可验证的数学命题，并用 TLC 穷举验证。

## 为什么需要它

五道防线都是「运行时检查」：它们在推进阶段的那一刻判断要不要放行。但如果状态机本身的
推进关系有洞（比如某个组合可以绕过门卫），再多防线也白搭。形式化这一层回答的是：

- 「交付必然意味着一次**新鲜的、全绿的**测试」是**所有可达状态**都成立，还是只在常见
  路径上成立？
- 「阶段不会跳级」「交付即终态」这些约束是规范里的假设，还是实现真的保证？

## 不变量清单

规范文件：`anti_shortcut/formal/PhaseBarrier.tla`，TLC 配置：`PhaseBarrier.cfg`。

| ID | 不变量（TLA+ 名） | 命题 | 实现侧的保证点 |
|----|------------------|------|----------------|
| — | `INV_TypeOK` | 类型正确（阶段 / 版本号 / 布尔旗标都在取值范围内） | `StateManager` 反序列化 + `skill` 校验 |
| INV-1 | `INV_DeliveryNeedsFreshTest` | 交付 ⇒ 测试通过且测试版本 = 当前代码版本 | `advance_stage` 的 4→6 / 5→6 分支（新鲜度校验） |
| INV-2 | `INV_CodeOnlyAfterImplementation` | 改过代码 ⇒ 阶段 ≥ 3 | `check_write_permission` 的阶段门禁 |
| INV-3 | `INV_DeliveryNeedsBehaviorAudit` | 交付 ⇒ 防线 4 已通过 | `defense.behavior_audit` 触发点 |
| INV-4 | `INV_StageGates` | 阶段 ≥ 2 ⇒ 防线 1/2/3 已通过 | `requirement_template` / `dual_review` / `formal_check` 触发点 |
| INV-5 | `INV_DeliveryNeedsHumanReview` | 交付 ⇒ 防线 5 已通过 | `defense.human_review` 触发点 |
| INV-6 | `INV_StageBound` | 阶段始终在 0..6 | `advance_stage` 的「只能 +1」约束 |
| INV-7 | `INV_DeliveredIsFinal` | 交付 ⇒ 阶段 = 6（终态） | `skill.is_complete` |
| INV-8 | `INV_TestedVersionBound` | 被测版本 ≤ 当前代码版本 | `mark_source_change` / `mark_test_run` 时间戳单调 |
| INV-9 | `INV_TestPassedImpliesRun` | 报告测试通过 ⇒ 确实有过代码和测试运行 | 测试证据校验（不允许空口宣称） |

## 规范里的抽象（为什么可以有限）

TLA+ 模型必须**有限状态**才能被 TLC 穷举完：

- 用**代码版本计数** `codeSeq` / `testedSeq` 替代墙钟时间戳。若直接用无界的 `Nat` 时钟，
  状态空间无限，TLC 永不终止（这是建这个模型时踩过的坑，已固化在规范注释里）。
- `MaxSeq = 2` 个代码版本足够覆盖每个门卫分支：`写码 → 测试 → 交付`，以及
  `写码 → 测试 → 修复 → 写码 → 测试 → 交付`。
- 交付后不再建模工具调用（实现里并不硬冻结工作区），因此「交付时测试新鲜」由专门的
  变量 `freshAtDelivery` 承载。
- 规范采用**严格配置**：五道防线都视为启用，「防线缺失/被关掉」不会被建模成「已通过」。

允许的阶段推进：`0→1 1→2 2→3 3→4 4→5 4→6 5→6`，其中 `4→6` 是文档化的
「测试全绿、跳过修复阶段」捷径，其余组合必须被拒绝。

## 本地运行 TLC

```bash
# 需要 JDK 17+ 与 tla2tools.jar
curl -fsSL -o /tmp/tla2tools.jar \
  https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar
java -jar /tmp/tla2tools.jar -config anti_shortcut/formal/PhaseBarrier.cfg \
  anti_shortcut/formal/PhaseBarrier.tla
```

通过时输出形如：

```text
1149 states generated, 168 distinct states found, 0 states left on queue.
No error has been found.
```

`tests/test_formal_alignment.py::test_tlc_model_check_passes_when_jar_is_available` 会在
环境变量 `PHASE_BARRIER_TLC_JAR` 指向 jar（或 `tlc` 在 PATH 上）时真实跑一次 TLC；
没有 TLC 时该用例自动跳过，其余对齐测试仍然有效。

## 实现 ↔ 规范对齐

`anti_shortcut/formal/__init__.py` 把两侧的元信息都变成机器可读的，`alignment_report()`
输出可直接序列化的 JSON：

- `SPEC_STAGE_GATES`：规范侧声明的阶段门卫（推进组合 → 必须通过的防线）；
- `ALLOWED_TRANSITIONS`：允许的阶段推进；
- `impl_stage_gates`：从实现侧 `DefenseLine.trigger` 反推出来的门卫表；
- `declared_invariants` / `configured_invariants` / `unchecked_invariants`：**定义了但没配进
  `.cfg` 的不变量会被点名**（定义了却不检查 = 没有保护）。

`tests/test_formal_alignment.py` 分三层验证：规范自身自洽、门卫表两侧一致、真实
`AntiShortcutSkill` 的运行时行为符合规范（不允许跳级、`4→6` 要求测试新鲜全绿、交付即终态）。

## CI 集成

`.github/workflows/ci.yml` 的 `formal-invariants` job：装 JDK 17 → 下载 `tla2tools.jar` →
跑 TLC 把配置里的 10 条不变量全部检查一遍。TLC 报错即构建失败。

## 反向验证（证明不变量不是空话）

只跑「全部通过」不能说明不变量有约束力。构造一次反例即可证明：把
`DeliverFromTestRun` / `DeliverFromRepair` 里的 `defense[4] /\ defense[5]` 删掉，TLC 立刻
报 `Invariant INV_DeliveryNeedsBehaviorAudit is violated`（退出码 12）——说明这条不变量
确实挡在「交付」这条路径上，而不是恒真式。
