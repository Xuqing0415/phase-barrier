# Verified 预注册-20：执行报告（正式批运行中）

## 执行摘要

- 预注册 20 实例、12 仓库；正式批实际执行 N 个、覆盖 K 个仓库：待填。
- 全量完成时，新增独立样本 n=18；2 个跨批复用实例单列。部分执行时按实际完成的独立配对数报告。
- 三批计划涉及 49 个不同实例，不称为 49 个相互独立的观测；该数不是当前已完成正式实验数。
- 镜像 20/20 就绪，运行 namespace 为 `logicstar`；来源、哈希与源码审计见
  `benchmarks/swebench/results/verified20_environment.json`。预注册清单未修改。
- pilot 只验收链路，不参与正式批统计，不按解决率筛选样本。
- 正式批于 2026-11-10 16:50:16（北京时间）启动：20 实例 × baseline/gated，
  共 40 项任务，并发 2、最多 60 轮。实际 runner PID `26980` 与状态文件一致，
  监督器 PID `21952`；首项 Astropy 双组已开始，完成数以文末运行快照为准。
- 启动后核对：16:54:30 已确认两进程存活、PID 记录一致且 gated 进入评分；
  16:55:54 补充保存人工核对凭据 `formal20_logicstar/operator_checkpoints.jsonl`，
  CSV 可读，Astropy gated 四项审计产物齐备，尚无完成评分或基础设施失败。
  每小时及每 5 配对监控已启用，下一小时检查最迟为 17:50（北京时间）。

## 三批样本去重

按两个已提交结果 CSV 和预注册清单的 `instance_id` 集合核验：

| 批次 | 原始实例数 | 与此前批次重叠 | 去重后新增 |
|------|-----------:|---------------:|-----------:|
| Lite Scale-20 | 20 | 0 | 20 |
| Verified 缓存-15 | 15 | 4（与 Lite） | 11 |
| Verified 预注册-20 | 20 | 2（与此前批次） | 18 |
| 合计（预注册全量完成时） | 55 | 6 | 49 |

计算为 **55 − 4 − 2 = 49**。预注册批内部没有重复行；seaborn/flask 是跨批复用。
去重只用于实例数量说明，原始运行和重复结果均保留，不合并 pooled 成功率或检验。

## 跨批结果一致性

来源：`scale20_lite_container.csv` 与 `verified15_cached.csv`。
以下单元格依次为 baseline / gated 的官方结果。

| 重叠实例 | Lite Scale-20 | Verified 缓存-15 | 两组结果是否均一致 |
|----------|---------------|-----------------|-------------------|
| astropy__astropy-12907 | resolved / resolved | resolved / resolved | 是 |
| astropy__astropy-14182 | resolved / resolved | resolved / resolved | 是 |
| django__django-10914 | resolved / resolved | resolved / resolved | 是 |
| scikit-learn__scikit-learn-10297 | resolved / resolved | resolved / resolved | 是 |

前两批这 4 个重复实例的二元评分一致率为 4/4；按模式比较为 8/8。
这 4 个实例不在本批预注册清单中，不存在它们的“正式批重跑一致率”；本批实际复用为 2 个实例、4 项任务。
这不等于补丁、轮数或行为轨迹一致，也不能证明 Agent 没有非确定性。

另行记录 pilot 与正式批的运行差异：`astropy__astropy-14369` 在 pilot 中双组
unresolved（gated 归类 A），正式批中双组 resolved（baseline 34 轮，gated 37 轮、
4 次拦截、阶段 6、13,887 字符，note=graded）。正式两组 FAIL_TO_PASS 均为 3/3，
PASS_TO_PASS 均为 732/732。该翻转是同一实例跨运行结果变异的直接观察，与 Agent
非确定性相容；两次运行间存在代码修复及可能的环境差异，尚不能确定变异来源。
不将差异归因于门禁效应，不在运行期追查根因；pilot 不作为正式统计，正式批结果
独立记录。详见 [Astropy 三次运行记录](verified20-astropy-audit.md)。

### 预注册批复用实例（不计入本批独立统计）

| 实例 | 缓存-15 正式批 gated | 本批正式 gated | 是否一致 |
|------|----------------------|----------------|----------|
| mwaskom__seaborn-3069 | resolved，54 轮 | resolved，60 轮 | 是 |
| pallets__flask-5014 | resolved，39 轮 | resolved，28 轮 | 是 |

两个实例的 baseline 本批分别为 Seaborn 31 轮、Flask 35 轮，均官方 resolved，
与缓存-15 baseline 的 resolved 一致。因此本批两个复用实例双组结果与缓存-15 为 2/2 一致。
前两批的 4 个重叠与这 2 个复用
是不同实例，不应混写成只有 4 个跨批重复实例。
早先 pilot 中 seaborn 曾 unresolved（60 轮），flask 曾 resolved（54 轮）；
该历史与缓存-15 正式批分开保留。若存在差异，记录轮数上限、模型参数及环境等可能因素，
未做控制实验前不确定原因，不直接归因于门禁。
其中 Flask 的 54 轮来自单实例 `pilot_one_results.csv`；后续三次结果对照统一采用
缓存批 `pilot5_results.csv`（Flask gated 为 15 轮）、缓存-15 与本批，明确区分 pilot 来源。

| 实例（gated） | 缓存批 pilot-5 | 缓存-15 | 本批正式 | 结果一致性 |
|---|---|---|---|---|
| seaborn-3069 | unresolved，60 轮，阶段 3 | resolved，54 轮 | resolved，60 轮 | pilot 与后两次不同 |
| flask-5014 | resolved，15 轮 | resolved，39 轮 | resolved，28 轮 | 三次一致 |

Seaborn pilot 在 60 轮上限结束、门禁未完成，与后两次形成运行间结果变异。
“触达轮数上限”是记录事实，但缺少增加预算的对照，不能断言 unresolved 仅由截断造成，
也不把它直接解释为门禁效应。Flask 单实例 pilot 的 54 轮运行同样 resolved，
但它与表中的 pilot-5 不是同一次运行，另保留来源。上述变化与 Astropy 的跨运行翻转并列报告。

## pilot 链路验收

| 验收项 | 标准 | 状态 |
|--------|------|------|
| 运行完整性 | 5 实例 × 2 模式完成；最终尝试无容器异常退出或模型调用错误 | 10/10 完成；历史中断另保留 |
| 评分链路 | PB_GRADE_NOTE 与 CSV、官方报告、原始日志一致，无冲突误判 | 10/10 graded，最终 chain audit 通过 |
| 门禁链路 | gated 有拦截与成功阶段推进；核对阶段和完成标记 | 通过；Astropy A 类及 SymPy 零拦截另行审查 |
| 复用实例保护 | 正式启动前四个实际输出目录均不存在或为空，保存检查凭据 | 四个目录均通过，凭据已保存 |

运行曾发生 runner 消失和 Docker 引擎不可用，具体中断原因未确定。
已保留中断记录、唯一完成的评分及未完成产物备份，恢复后仅续跑未完成项。
不能把此次恢复写成“整个历史从未异常”；验收须区分已恢复的中断与最终尝试的完整性。

审计规则由 `scripts/verified20_checks.py` 实现，并已接入本地监督器调用的审计与启动脚本：

- `official_resolved_gate_incomplete`：只标记，不因该组合否决 pilot。
- `gate_complete_official_unresolved`：按本实验验收约定阻止正式启动，分析门禁证据与官方评分差异；此标记本身不证明漏洞。
- 未完成且停在阶段 2/3：单独分析；十万字符及以上补丁另标记 `large_patch`。
- 正式批使用新输出根目录；seaborn/flask 的 baseline/gated 四个目录逐项检查。
  非空即拒绝启动，不删除历史证据；runner 自身清理与 `patch_is_fresh()` 仍生效。

### 已观察到的 pilot 异常组合

> Django gated 组官方 resolved，但 `gate_completed=0`，结束于阶段 4。
> 该实例的 resolved 结果不纳入“门禁全流程有效”的证据，仅作为“门禁未阻断官方评分”的观察。

该运行达到 60 轮上限，8 次拦截，耗时 509.1 秒；补丁为 4,972 字符，非十万字符级异常。
官方 `PB_GRADE_NOTE=graded`，FAIL_TO_PASS 为 1 成功 / 0 失败，PASS_TO_PASS 为 6 成功 / 0 失败。
这些是 pilot 观察，不能预填到正式批 Django 结果中。

### pilot 已完成记录（阶段性快照）

| 实例 | 组别 | resolved | gate_completed | 轮数 | 耗时秒 | 拦截次数 | 补丁字符数 | note |
|------|------|---------:|---------------:|-----:|-------:|---------:|-----------:|------|
| django__django-16901 | gated | 1 | 0 | 60 | 509.1 | 8 | 4972 | graded |
| django__django-16901 | baseline | 1 | 0 | 31 | 166.9 | 0 | 1395 | graded |
| sympy__sympy-18211 | gated | 1 | 1 | 35 | 330.0 | 0 | 2187 | graded |
| sphinx-doc__sphinx-9281 | baseline | 1 | 0 | 34 | 835.9 | 0 | 2480 | graded |
| sympy__sympy-18211 | baseline | 1 | 0 | 49 | 1551.4 | 0 | 1771 | graded |
| sphinx-doc__sphinx-9281 | gated | 1 | 1 | 33 | 141.8 | 5 | 2372 | graded |
| matplotlib__matplotlib-23476 | baseline | 1 | 0 | 35 | 333.7 | 0 | 1401 | graded |
| matplotlib__matplotlib-23476 | gated | 1 | 1 | 53 | 390.2 | 11 | 1720 | graded |
| astropy__astropy-14369 | gated | 0 | 1 | 36 | 510.5 | 5 | 14618 | graded |
| astropy__astropy-14369 | baseline | 0 | 0 | 60 | 676.1 | 0 | 15530 | graded |

运行中的 `results.csv` 在每个配对任务完成时追加并 flush 一行；上表只是已核对快照，
历史阶段性表格不是独立验收依据，最终收尾和 chain audit 结果见后文。
baseline 的 gate_completed=0 不用于门禁有效性评价。
SymPy gated 的零拦截需单独审查：日志在第 24、28、31、35 轮分别成功进入
阶段 2、3、4、6，最后因测试通过跳过修复阶段而进入交付。
该轨迹说明门禁有参与，零拦截本身既不能证明绕过，也不能证明没有绕过；
最终按现存日志和补丁审查归类 benign，证据限制及复核结果见下文。

五个 pilot 实例均包含在正式预注册 20 个中；正式批使用独立输出目录全部重新执行，
不合并、不跳过 pilot 已解决实例。最终报告须披露这五个实例的先前 pilot 暴露，
“新增 18”是相对于前两批正式实验的实例身份口径，不表示所有实例从未运行过。

正式启动前必须同时确认 pilot 审计通过、运行代码已推送且 CI 全绿、Docker 稳定、
四个复用实例/模式输出目录检查通过。旧自动启动监督器已停止，pilot runner 不受影响。
若本次续跑期间 Docker 再次出现 socket 异常，正式批前先恢复/重启并重新核验。
正式批每小时核对 PID 和 stale 判定，每完成 5 个双组实例核对 note 分布；
发现评分基础设施错误即停止推进并排查，低拦截实例单列分析。

### 拦截完整性审计发现与计数口径

上述表中的拦截值保留原始计数，不作为已校准指标。只读审计发现 runner 在捕获
`PermissionError` 时先加一，生成 `gate_denied` 结果后又加一，同一工具拒绝被重复计数。

| pilot gated 实例 | 原始计数 | 日志工具拒绝事件 | 阶段推进拒绝事件 | 两类事件合计 |
|------------------|---------:|-----------------:|-----------------:|-------------:|
| Django-16901 | 8 | 2 | 4 | 6 |
| Matplotlib-23476 | 11 | 3 | 5 | 8 |
| Sphinx-9281 | 5 | 1 | 3 | 4 |
| SymPy-18211 | 0 | 0 | 0 | 0 |

原 pilot 产物只保存补丁、stats 和截断的 stdout，未导出门禁 state 或独立 trace。
Skill 的可选 behavior trace 默认关闭，且其 tool_call 记录不等同于 blocked 事件，
不能用 `grep blocked` 代替明确的工具拒绝与阶段拒绝口径。
原 pilot 完成后已修复计数并持久化审计事件；修复状态及新的容器验证见下文。
历史批次拦截成本也可能受相同实现影响，不能把修复前后的计数直接比较。

SymPy gated 为 35 轮 / 2187 字符，baseline 为 49 轮 / 1771 字符；
补丁不是小于 100 字符的表面改动。日志显示先写 spec、再写复现测试、
阶段 3 才修改实现、阶段 4 执行测试，4→6 是状态机支持的“测试通过无需修复”转换，
不要求人为经过阶段 5。已有信息未证明绕过，但缺少完整参数和持久化状态，
历史快照曾暂标记 `zero_interception_suspicious`（证据不足待复核）；最终审查见下文。
新复核的拦截值和归类单列，不将新运行轨迹冒充原 pilot 的完整历史。

- pilot 原始字段保持不变；事后按 stdout 重算为 Django 8→6、Matplotlib 11→8、
  Sphinx 5→4、Astropy 5→4。上方表格仍保留原始值，重算值不伪称来自旧 trace。
- 正式批使用修复后的逻辑，`gate_intercepts` 须与 `trace.jsonl` 中 `blocked=true`
  的事件数一致（事件类型为 `blocked`）。
- **pilot 平均拦截次数不能与正式批直接比较**；本报告拦截成本统计仅引用正式批数据，
  不混入 pilot、审计复核或修复前历史批次。

### pilot 收尾与修复状态

pilot 于 2026-11-10 14:53:52（北京时间）完成，10/10 配对任务均为 `graded`；
baseline 与 gated 各 4/5 resolved，仅描述 pilot，不作为正式统计。
Astropy gated 的 `gate_completed=1` 但官方 unresolved，触发约定的验收阻断。
补丁成功应用，官方报告 `infra_failure=false`，FAIL_TO_PASS 为 2 成功 / 1 失败，
PASS_TO_PASS 为 730 成功 / 2 失败；因此不能作为评分基础设施错误重试至通过。
未通过的 FAIL_TO_PASS 包括 `test_cds_grammar_fail[km/s.Mpc-1]`。
门禁只证明 Agent 提交的测试证据满足当时交付条件，不保证覆盖官方全部测试。
后续同补丁重放已完成，归类 A 的依据见下节；保留该失败观察。

原 pilot 完成后，runner 已修复同一 PermissionError 重复计数，并新增持久化
`trace.jsonl`（每次工具调用的参数、前后阶段、拒绝类型及结果状态）及最终 `state.json`。
工具拒绝与阶段推进拒绝统一各计一个 blocked 事件，日志落盘失败不静默忽略。
新增测试覆盖异常拒绝与返回值拒绝两条路径、计数与 trace 一致、阶段记录、密钥脱敏和写盘失败。
最初 44 项相关测试通过；后续含审计产物解析与交付新鲜度的相关测试共 56 项通过，
并完成 Astropy、Django 两个真实镜像的确定性验证（各 3 个事件、2 个 blocked，零模型请求）。
这不替代下述 SymPy/Astropy 模型运行复核。
原 pilot 历史计数保持原样，未回填修复后的新定义。

## 异常实例：astropy-14369（pilot gated）

- 门禁：`gate_completed=1`，阶段 6，36 轮，14,618 字符。
- 官方：unresolved，FAIL_TO_PASS 2/3 通过，PASS_TO_PASS 730/732 通过；
  补丁成功应用，`infra_failure=false`。
- 归类 A：`gate_completed_insufficient`。专项证据见 [Astropy 审计](verified20-astropy-audit.md)。
- 同一补丁重放：Agent 自写测试 4/4 通过，原有语法失败测试 20/20 通过。
  官方 FAIL_TO_PASS 包含 Agent 未覆盖的“混用除号与点乘应抛 ValueError”场景。
- 未修改既有测试文件，排除该补丁的 `test_tampering`。旧 state/trace 缺失，
  无法逐时间戳核验；未发现 `delivery_clean` 漏洞的具体证据，不补造旧历史。
- baseline 同实例也 unresolved，官方失败测试与 gated 相同；该观察不支持将失败归因于门禁。
- 门禁验证的是已提交测试证据，官方隐藏场景不在门禁可见范围内。
  门禁通过是 gated 流程合规交付的必要条件，不是官方通过的充分条件。
  该实例不作为“门禁有效”或“门禁有害”的证据，用于说明能力边界。

## 门禁完成但官方未通过：审计汇总与收尾安排

截至 2026-11-13 10:46，完成 21/40 项，正式批该组合为 2 项；下表分开历史 pilot 与正式批，后续新增项汇入同一表。
仅凭 `gate_completed=1` 与官方 unresolved 不能确定 A/B/C，更不能把“门禁通过”当作官方 resolved 的必要条件。

| 批次 | 实例（gated） | 门禁 / 官方结果 | 归类与证据状态 |
|---|---|---|---|
| pilot | astropy-14369 | 阶段 6，门禁完成；官方 unresolved | A，覆盖不足；同补丁重放证据见专项审计，不计入正式批该组合数量 |
| 正式批 | requests-2317 | 27 轮，阶段 6，4 次拦截，4,524 字符；官方 unresolved，note=graded | `env_tls_interference`；F2P 7/8、P2P 127/133；baseline 和 gated 重放均有证书错误，单列于 A/B/C 之外 |
| 正式批 | xarray-3993 | 34 轮，阶段 6，6 次拦截，4,021 字符；官方 unresolved，note=graded | A 类 warning 要求差异 + B 类 `test_assertion_weakened`，分别记录；F2P 0/2、P2P 2398/2398；重放证实登记断言被移除后最终测试通过 |

正式批 Astropy 双组均 resolved，不列作正式批门禁完成却官方失败的实例。
每次监控按 CSV 字段名核对新增组合，只标记、不停批；基础设施错误仍按已有根因规则停止。

已完成两项证据初查及独立容器同补丁重放：[统一审计记录](verified20-formal-gate-audit.md)；
共用 [A/B/C 模板](verified20-gate-audit-template.md) 已准备。
两项持久化状态、计数和阶段连续性核验通过，保存状态上的 `delivery_clean()` 均为 true；
这些检查不替代测试覆盖审查。Requests 自写测试重放 6/6 通过，官方失败测试在同一容器两次均报 TLS 证书错误，
baseline 原日志也有同类错误，单列为环境混杂，不归 A。
Xarray 最终自写测试重放 3/3 通过，阶段 2 原始版本为 2 通过、1 失败；
两个官方 F2P 均复现 FutureWarning/DeprecationWarning 不符。断言弱化与 warning 行为差异分开记录，
不能只以“未改官方测试”排除测试完整性问题，也不强行合并成单一 A 类。
Astropy 仍只将历史 pilot 归 A，不写成“正式批三个均为 A”。
初查证据清单 `formal_gate_preliminary_audit.json` 保留，追加的重放及 Seaborn 对照凭据为 `formal_parallel_audit_evidence.json`。

40 项官方证据核验后，先复核新增异常及上述审计的证据完整性；对后续新增项按同一流程核对 state、trace、补丁和官方报告，
必要时在独立目录重放，原评分不覆盖。A 为覆盖不足，B 为测试完整性问题，C 为 `delivery_clean` 缺陷；不能预填 A。
若确认 B/C，先保留本批证据并披露受影响范围，再修门禁和验证；修复后的运行单列，不覆盖已有正式结果。
表格自动填充与机制审计分开，存在待审计项时不宣称机制审查已定稿。

### Seaborn 运行差异：已完成证据对照

缓存批 pilot-5 gated 为 60 轮、阶段 3、官方 unresolved；正式批 gated 同为 60 轮，
但为阶段 4、`gate_completed=0`、4,805 字符、官方 resolved。轮数上限是两次运行的共同现象，不能单独解释结果差异。
pilot 补丁为 4,312 字符，F2P 1/2、P2P 94/94；正式补丁为 4,805 字符，F2P 2/2、P2P 94/94。
pilot 的失败断言要求显式 y 范围 `(-1, 2.1)` 保持顺序，实际错误反转为 `(2.1, -1.0)`；
正式补丁不改显式范围，自写测试也覆盖该行为。该具体补丁差异解释了原官方失败的断言结果。
pilot 旧 state/trace 缺失，保留 stdout 证据限制；正式 trace 显示后期多次推进被“未检测到测试运行记录”拒绝。
完整阶段、轮数、补丁与测试对照见 [Seaborn 专项对照](verified20-seaborn-comparison.md)，不把新轨迹当作旧历史。
未做控制实验前，原因保留为未确定，不单独归因于模型采样、中间代码修复或门禁开销。

## SymPy/Astropy 审计复核（不并入正式统计）

使用修复后的独立输出目录 `.pytest_tmp/verified20/audit_revalidation/`，
验证新 `state.json` / `trace.jsonl` 持久化、拦截计数和零拦截归类。
复核结果不并入正式批统计，不复用其补丁或 resolved 结果；正式 20 实例全部独立执行。

| 核验项 | 结果 |
|--------|------|
| 复核 runner 与两项官方评分完成 | 已完成；宿主 runner 曾消失，保留中断证据并仅恢复原补丁评分，零新增模型请求 |
| SymPy 原 pilot 零拦截 | `zero_interception_benign`，按现存日志与补丁解释；旧 state/trace 缺失的局限保留 |
| Astropy/SymPy 的 stats、state 与 trace 一致 | 均通过；分别为 51/52 个事件、3/1 次 blocked |
| Docker 复核期间稳定，无新增 stale socket | 当前引擎及 PostgreSQL 健康，复核时段日志未发现 stale socket/引擎失败；runner 中断原因未知 |

SymPy 新复核：36 轮、1,761 字符、阶段 6、官方 resolved，FAIL_TO_PASS 1/1、
PASS_TO_PASS 54/54。原 pilot 的 0 次与新复核的 1 次分开保留，
详见 [SymPy 零拦截审计](verified20-sympy-audit.md)。
Astropy 新复核：44 轮、15,625 字符、阶段 6、官方 unresolved，FAIL_TO_PASS 2/3、
PASS_TO_PASS 731/732；对新补丁单独核验交付新鲜度和覆盖差异后也归类 A。
以上均为审计复核数据，不进入正式结果。

## 正式启动前置条件与运行记录

| 条件 | 状态 / 凭据 |
|------|-------------|
| `4b71b20a7f8bfa806b09d4379cce973f2d19b66f` 已推送且 CI 全绿 | CI、Security、Plugin CI、docs 四项全部 success |
| pilot 审计通过，Astropy A 类专项审查完成 | 通过，10 行无 errors/manual review；专项报告已落盘 |
| Docker 稳定，复核已结束 | 通过；无残余复核工作负载，20 个镜像 ID/RootFS 再校验一致 |
| seaborn/flask × baseline/gated 四个实际输出目录不存在或为空 | 通过；正式输出中的 `reused_patch_preflight.json` 保存四项凭据 |
| 新审计链路验证通过 | 通过；两个真实镜像确定性验证、两项模型复核，本次重跑 74 项相关测试通过 |

主 CI 凭据：[CI 42774476828](https://github.com/Xuqing0415/phase-barrier/actions/runs/42774476828)。
五项完整凭据在 `.pytest_tmp/verified20/formal_prelaunch.json`；
本次相关回归覆盖审计产物、计数、复用保护、容器 runner 和交付后改码/红测路径。

附件中的 `--manifest`、`--output-dir`、`--max-rounds` 和
`--preflight-check-reuse-instances` 不是当前容器 runner 的参数。
实际启动器为 `.pytest_tmp/verified20/run_verified_stage.py formal`，内部先检查四个复用目录，
再使用 runner 的 `--tasks`、`--dataset`、`--results`、`--agent-runs`、`--eval-runs`、
`--modes baseline,gated` 和 `--max-turns 60`；运行时仅替换镜像 namespace 为 `logicstar`。
正式输出独立保存在 `.pytest_tmp/verified20/formal20_logicstar/`，进度文件名为 `results.csv`。

启动后 5 分钟内核对 `launch.json`、`status.json` 与实际 runner PID 一致，
并确认第一个实例已开始运行；PID 缺失或不一致立即停止并修复后再启动。
每小时核对 runner 存活、stale 状态、完成配对数和 note 分布，每 5 个配对实例复查一次。
出现 `grade_infra_failure` 即停止推进并排查；零拦截 gated 实例和超过 60 分钟的实例单列。
中断时记录时间点，保留已完成评分与证据，修复后仅续跑未完成项。
正式批后台监督器每秒检查新增 note，按每小时/每 5 配对保存监控快照，
异常时停止该批进程和容器；其他 Docker 服务保留。
正式数值和审计摘要自动写入输出目录及本文末尾，最终机制解释仍须复核异常证据。

### 2026-11-10 正式批中断与断点恢复

17:45:59（Asia/Shanghai）监督器因 Matplotlib-23476 baseline 的 `empty_patch` 停止该批。
容器日志显示模型调用重试后出现 `URLError: [Errno -2] Name or service not known`，
该次空补丁归为 `model_call_error`，不作为 Agent 主动放弃或官方 unresolved。
Django-16901 gated 同期也有模型调用错误，但 6,054 字符补丁及门禁审计已保存，官方评分未完成。
中断时保留 9 项已完成官方评分（其中包含 4 个完整配对），不是 13 项；
恢复清单为 `skip=9`、`regrade_only=1`、`rerun=1`、`pending=29`，合计 40 项。
4 个完整配对内 baseline/gated 均为 3 resolved、1 unresolved；共同失败的是 Django-16631，
因此当时 baseline-only 与 gated-only 均为 0。

恢复前核验宿主机 DNS、容器 DNS 和 Docker daemon 配置。
默认容器 DNS 连续探测 10/10 通过，显式 DNS 容器探测另有 10/10 通过；
实际模型端点 `api.deepseek.com` 的 TLS 连接及证书校验 3/3 通过，未发送模型请求。
宿主 Docker 配置未设置显式 DNS，未修改 daemon 或重启 Docker。
续跑 Agent 容器采用 `--dns 223.5.5.5 --dns 1.1.1.1`。
这些检查表明当时连接验证通过，并不证明间歇性 DNS 故障的根因已永久消除。
证据保存在 `.pytest_tmp/verified20/` 下的 `dns_diagnostic_default.json`、
`dns_diagnostic_override.json` 和 `model_transport_check.json`。

运行规则改为按根因判定：`grade_infra_failure` 或模型调用基础设施错误立即停止；
`empty_patch` 且 `agent_gave_up` 仅标记；根因不明的空补丁连续两项才停止。
非空补丁如伴随模型调用失败也保留并停止，恢复时优先只补评分。
零拦截、超过 60 分钟、补丁至少 100,000 字符、门禁与官方结果不一致均单列复核。
真实 Matplotlib 中断日志分类为 `model_call_error`，停止策略验证通过；
相关回归先有 92 项通过，最终修复后的变更相关子集为 59 项通过。
运行中修改了编排、分类及监督规则，未修改 Agent 核心；源码哈希随恢复记录保存，
运行间比较须保留这一代码差异信息。

18:20:57 开始恢复准备与 Django 保存补丁的补评分，监督器 PID 为 3916。
Matplotlib 原失败目录移入 `formal20_logicstar/recovery_20261110T102057Z/`，
原始 CSV、状态、启动信息、日志及 62 个已完成证据文件的哈希均归档。
恢复启动预检曾分别因模块导入和计划相对/绝对路径表示差异退出；
两次均发生在评分与模型调用前，修复并保留错误日志后才继续。
首小时每 15 分钟保存监控快照，其后每小时，且每 5 个完整配对额外保存一次；
基础设施停止条件持续每秒检查。最终恢复时间和新 runner PID 以 `launch.json` 的
`resume_attempts`、`pid_verification.json` 和 `supervision.json` 为准。

20:43:41 正式恢复剩余 30 项 Agent 任务，runner PID 2540、监督器 PID 3916，
`launch.json`、`status.json` 与实际 PID 一致。Django-16901 gated 使用原补丁补评分，
官方 resolved，FAIL_TO_PASS 1/1、PASS_TO_PASS 6/6，未重跑 Agent。
其 `gate_completed=0`、最终阶段 5，标记 `official_resolved_gate_incomplete`，
且保留此前模型调用失败的中断背景，不作为门禁全流程有效的证据。
恢复验收时原 9 项评分行逐项一致，62 个证据文件哈希全部未变；
当前 10 项均 `graded`，5 个完整配对内 baseline/gated 各 resolved 4 项，
baseline-only 与 gated-only 均为 0。这些为中途结果，尚非全批结论。
实际 Matplotlib baseline/gated 容器均已核验显式 DNS 参数，活动输出目录为新建，
gated 的 `trace.jsonl` 已开始更新；下一次计划监控为 20:58:41。

### 2026-11-13 再次恢复与运行期修复

11 月 10 日 21:27:29，Seaborn-3069 baseline 的模型调用重试后出现
`URLError: [Errno -3] Temporary failure in name resolution`。它已生成 2,240 字符补丁，
记录为 `model_call_error`，未将模型错误直接算成官方 unresolved。
监督器随后停止 runner，但清理 Docker 时用 Windows 默认 GBK 解码 UTF-8 JSON，
触发 `UnicodeDecodeError` 及后续 `TypeError`，未能把 `status.json` 从 running 更新为 halted。
11 月 13 日检查确认旧 runner 和监督器均已不存在，Docker 中没有该批活动容器；
因此旧 running 是残留状态，不能据此认为实验一直在运行。

实际恢复清单为 13 项已完成评分、2 项保存补丁只补评分、0 项重跑、25 项待开始，
合计 40 项。Matplotlib-24970 baseline 保存了 1,038 字符补丁与统计，缺失宿主容器日志；
统计记录 37 轮、220,038.7 秒，文件在 11 月 13 日才更新。
这项跨中断耗时的具体构成未确定，保留原值并标记超过 60 分钟，
不能当作正常模型执行时长或用它推断执行效率。

修复停止链路：先持久化停机状态，再逐项清理容器；Docker inspect JSON 按原始字节解析，
不依赖 Windows 活动代码页；单个容器检查失败不会中断后续清理。
容器归属按本批挂载目录判断，避免仅凭通用标签误停其他批次。
运行日志改为持续落盘，以便容器或宿主进程中断时保留部分输出。
停止判定同时识别复合 note 中的 `model_call_error`，即使日志缺失也不漏停。
恢复计划、已保留项数和新增行偏移改为动态计算，baseline 补评分不再要求 gated 审计产物。
上述修复后 78 项相关测试通过；汇总工具另有 7 项测试通过。
编排和监督代码的运行期变更保留源码哈希，不把本次恢复视为与初始运行完全相同的环境。

本次宿主 DNS 核验完成，默认 DNS 与显式 DNS 的容器解析分别 10/10 通过，
实际模型端点 TLS 及证书校验 3/3 通过，未发送诊断性模型请求。
此前显式 DNS 仍发生过故障，因此此次验证不等于永久修复，继续保留模型错误即停策略。
旧诊断和错误记录已归档，恢复目录为 `formal20_logicstar/recovery_20261113T021426Z/`，
其中保存 13 项完成评分及 92 个证据文件的哈希。

10:19:55（Asia/Shanghai）恢复剩余 25 项 Agent 任务，runner PID 16704、监督器 PID 31940，
PID 核验通过。两个补评分均未重跑 Agent：Matplotlib-24970 baseline 官方 resolved，
FAIL_TO_PASS 1/1、PASS_TO_PASS 253/253；Seaborn-3069 baseline 官方 resolved，
FAIL_TO_PASS 2/2、PASS_TO_PASS 94/94。此时累计 15 项 graded、7 个完整配对，
Seaborn baseline 的评分等待其 gated 结果后才进入配对统计。
首小时按 15 分钟保存快照，其后每小时及每 5 配对检查；基础设施错误每秒检查。

新增 `scripts/verified20_report.py` 从正式证据生成三批去重、独立/复用结果与成本、
跨批一致率和逐项异常；它拒绝把不完整数据定稿。
后台收尾监控等待本次 `completion_verification.json` 确认所有官方证据齐全后，
才更新主报告及 `docs/benchmarks/verified20-final-results.md`。
若门禁完成却官方 unresolved，仍保留 A/B/C 审计待办；零拦截仍须 benign/suspicious 复核，
自动表格生成不代替机制审计。收尾状态见 `formal20_logicstar/report_finalization_status.json`。

### 轮数预算与完整流程的观察

Django gated 的 14631、16333、16631、16901 分别为 60、60、60、53 轮，
最终阶段分别为 4、4、5、5，均 `gate_completed=0`；官方结果为 3 resolved、1 unresolved。
门禁未完成不能直接解读为官方评分受阻：3 个官方 resolved 可作为门禁未必阻断官方评分的观察，
不作为门禁全流程有效的证据。这一模式与轮数上限和多阶段开销的交互相容，
但没有增加轮数预算的对照实验，尚不能确定未完成的原因。

Matplotlib-23476 gated 为 58 轮、8 次拦截、阶段 6、`gate_completed=1`、1,328 字符，
说明接近 60 轮上限仍存在完成全流程的实例，轮数上限并非绝对阻断。
正式批最早完成全流程的是 Astropy-14369（37 轮、4 次拦截、阶段 6），
不把 Matplotlib 记为第一个完成的实例。Django 模式与上述完成案例并列呈现，避免把完成标志等同于官方解决率。

### 解决率与耗时的工程权衡（人工记录，自动刷新保留）

截至 2026-11-13 11:14，已完成 14 配对，正式批出现 1 个 baseline-only（Xarray-3993）：
baseline resolved、gated unresolved。该配对保留在正式结果及成本样本中，不因已知原因删除或改写。
同补丁重放分别证实阶段 3 替换阶段 2 的测试后丢失了同时传 coord/dim 应报错的断言，
以及自写测试要求 DeprecationWarning、官方要求 FutureWarning 的行为差异；二者分开记录，
不把断言替换说成官方 warning 失败的已证实原因。
gated 已进入阶段 6 并交付，并非停在未完成阶段；该轨迹仍不能隔离门禁对补丁选择的因果影响。
当前门禁允许测试演进而未保证既定行为断言覆盖不降低，属于设计边界；
单个配对不构成总体因果效应，整体方向、解决率与耗时均以最终全量结果为准。

在 2026-11-13 10:31 的中途成本快照中，独立部分有 7 个完整配对；
排除 Matplotlib-24970 baseline 的跨中断墙钟后，baseline 时间样本 n=6、gated n=7。
**gated 组 P90 耗时为 baseline 的 2.16 倍（1,163.7s vs 539.0s），约增加 116%。**
该快照的双组 resolved 均为 6/7，未观察到配对解决率下降，同时观察到明显耗时增加；
不能仅以解决率表述工程权衡。

上述 2.16× 是注明样本与时点的历史快照，不是冻结的全批结论。
新增 Requests 双组结果后，10:38 快照的独立部分为 8 个完整配对、双组各 resolved 6/8；
排除跨中断后的 baseline n=7、gated n=8，P90 为 587.0s 与 1,098.7s，比值更新为 1.87×。
最新与最终比值由下面的成本表和最终报告重算。这是两组 P90 的比值，
不是逐实例平均减速，也不能视为控制了测试、重试及环境影响的门禁纯因果代价。
有限中途样本中的解决率持平不足以证明总体不降低解决率；最终结论必须并列呈现解决率与成本。

最终成本数字仅在全部 40 项官方证据核验后确定，独立与复用分开。
全量独立配对 n=18；排除跨中断的 Matplotlib baseline 时间后，时间样本为 baseline n=17、gated n=18，
其他指标及该实例的官方结果仍保留。成本比基于有限样本 P90，P90 对离群值敏感；
2.16×→1.87× 是同一正式批中途随样本增加的漂移，不是不同批次间的成本比较。
中间数字与最终数字均视为点估计，不作为精确值或置信区间；这一限制须在最终局限章节保留。

<!-- FORMAL_COST_REVIEW -->

### 跨中断耗时：成本口径修正（自动更新）

截至 2026-11-13T03:55:22.204470+00:00；仅计双组均 graded 的配对，独立与复用分开。

matplotlib-24970 baseline 的 220,038.7 秒跨两次中断，不计入主要成本 P90；该实例单列，不作为执行效率证据。
原始秒数和其他指标保留。文末运行快照的 seconds 表是原始墙钟核对口径，主要效率口径见下表的排除行。

| 部分 | 口径 | baseline n | baseline P90 秒 | gated n | gated P90 秒 |
|---|---|---:|---:|---:|---:|
| 独立部分 | 全部实例（原始墙钟） | 16 | 846.2 | 16 | 789.8 |
| 独立部分 | 排除跨中断耗时 | 15 | 648.5 | 16 | 789.8 |
| 复用部分 | 全部实例（原始墙钟） | 2 | 898.4 | 2 | 423.5 |
| 复用部分 | 排除跨中断耗时 | 2 | 898.4 | 2 | 423.5 |

复用部分：排除跨中断耗时后，gated P90 为 baseline 的 0.47×（423.5s vs 898.4s；gated n=2、baseline n=2）。
独立部分：排除跨中断耗时后，gated P90 为 baseline 的 1.22×（789.8s vs 648.5s；gated n=16、baseline n=15）。
这是两组 P90 的描述性比值，不是逐实例平均减速，也不能隔离门禁、测试和模型重试各自的耗时贡献。部分结果不能证明总体解决率不降低；最终结论须同时报告配对解决率和成本。
最终成本结论须基于全部 40 项核验证据，独立与复用分开；全量独立配对 n=18，排除跨中断耗时后 baseline 时间 n=17、gated n=18。P90 对离群值敏感，样本量有限；同一正式批的中途估计曾随样本增加由 2.16× 漂移至 1.87×，这些中间数字及最终比值均为点估计，不是精确值或置信区间。

评分来源：`{"中断前已评分": 9, "恢复后评分（含补评）": 28}`。
来源按首次中断的归档评分键划分，完整分段成本见 `formal20_logicstar/report_preview.md`，最终表由同一程序生成。恢复后补评分沿用原 Agent 统计，不能将 9/31 的评分分段直接解释为新旧 Agent 运行分段或稳定环境对照。

<!-- END_FORMAL_COST_REVIEW -->

## 正式结果（待填）

| 批次 | 来源 | 配对实例数 | baseline resolved | gated resolved | baseline-only | gated-only | 备注 |
|------|------|-----------:|------------------:|---------------:|--------------:|-----------:|------|
| Lite Scale-20 | 预注册 | 20 | 18 | 20 | 0 | 2 | 与缓存批有 4 个重叠 |
| Verified 缓存-15 | 缓存可用性 | 15 | 12 | 13 | 0 | 1 | 含选择偏差；跨批新增 11 |
| Verified 预注册-20 | 预注册 | 待填（独立部分计划 18） | 待填 | 待填 | 待填 | 待填 | 另 2 个复用单列 |

实际执行数、缺失原因、两组均完成官方评分的配对数与仓库覆盖均待填。
容器失败、模型失败或缺失评分不得直接当作官方 unresolved。
逐实例报告轮数、耗时、拦截次数、补丁字符数、结束阶段、gate_completed 和 note；
按批次、模式列出成本分布及 P90，预注册批独立与复用实例分开。

## 局限与结论（待结果后定稿）

- 本批尚未全部完成，不用中途结果声称三批方向一致，也不宣称 p<0.05。
- 不同实例数不保证统计观测完全独立；仓库聚类、跨批复用与缓存选择偏差均需说明。
- 前两批 4 个重叠实例的二元结果一致性为 4/4；本批另外 2 个复用实例与缓存-15 双组一致为 2/2，pilot 差异另列。
- LogicStar 镜像、环境适配、运行中断与重试均作为环境信息记录，不更改样本。
- 容器无 chroot，Agent 可读门禁源码；门禁通过不等于官方通过，Astropy 案例说明测试集差异。
- 三批选择标准不同，三批结果并列报告，不合并 pooled 成功率或显著性检验。
- Xarray 证实阶段 2 登记的关键断言在阶段 3 被替换，最终测试通过而原登记断言失败；当前门禁未保证测试演进保持既定行为覆盖，列为设计边界和后续改进项，不与官方 warning 失败混为单一原因。
- 成本 P90 对离群值敏感、有限样本下中途比值由 2.16× 漂移至 1.87×；最终基于 40 项齐全证据重算，独立配对 n=18，排除跨中断耗时后的时间 n 为 baseline 17、gated 18，成本比仅为点估计。
- “100+ 实例”只能作为扩样规划，不能保证 p<0.05；所需样本量还取决于效应和配对不一致率。
- 若正式结果确实方向一致且未达显著，结论明确写方向一致但证据不足；若方向不一致，如实报告。

<!-- FORMAL_RUNTIME_SNAPSHOT -->

## 正式批运行快照（自动生成）

更新时间：2026-11-13T11:36:03.151215+08:00。状态：`running`。

此处仅引用正式输出；pilot 和复核均未并入。仅 note=graded 的双组配对进入以下结果表。

| 部分 | 已完成配对 | baseline resolved | gated resolved | baseline-only | gated-only |
|------|-----------:|------------------:|---------------:|--------------:|-----------:|
| 独立部分 | 13 | 11 | 10 | 1 | 0 |
| 复用部分（单列） | 2 | 2 | 2 | 0 | 0 |

### 正式批逐项记录

| 实例 | 模式 | resolved | 轮数 | 秒 | 拦截 | 阶段 | gate_completed | 补丁字符 | note |
|------|------|---------:|-----:|---:|-----:|-----:|---------------:|---------:|------|
| astropy__astropy-14369 | gated | 1 | 37 | 237.0 | 4 | 6 | 1 | 13887 | graded |
| astropy__astropy-14369 | baseline | 1 | 34 | 346.1 | 0 |  | 0 | 14064 | graded |
| django__django-14631 | baseline | 1 | 37 | 178.1 | 0 |  | 0 | 5495 | graded |
| django__django-16333 | baseline | 1 | 60 | 301.7 | 0 |  | 0 | 1994 | graded |
| django__django-14631 | gated | 1 | 60 | 598.5 | 3 | 4 | 0 | 10787 | graded |
| django__django-16333 | gated | 1 | 60 | 434.4 | 8 | 4 | 0 | 5227 | graded |
| django__django-16631 | baseline | 0 | 59 | 402.2 | 0 |  | 0 | 6491 | graded |
| django__django-16901 | baseline | 1 | 34 | 132.1 | 0 |  | 0 | 1240 | graded |
| django__django-16631 | gated | 0 | 60 | 676.1 | 2 | 5 | 0 | 11754 | graded |
| django__django-16901 | gated | 1 | 53 | 1554.1 | 9 | 5 | 0 | 6054 | graded |
| matplotlib__matplotlib-23476 | baseline | 1 | 60 | 675.7 | 0 |  | 0 | 1454 | graded |
| matplotlib__matplotlib-23476 | gated | 1 | 58 | 903.5 | 8 | 6 | 1 | 1328 | graded |
| matplotlib__matplotlib-24970 | gated | 1 | 60 | 366.3 | 10 | 5 | 0 | 5153 | graded |
| matplotlib__matplotlib-24970 | baseline | 1 | 37 | 220038.7 | 0 |  | 0 | 1038 | graded |
| mwaskom__seaborn-3069 | baseline | 1 | 31 | 983.0 | 0 |  | 0 | 2240 | graded |
| pallets__flask-5014 | baseline | 1 | 35 | 136.7 | 0 |  | 0 | 850 | graded |
| pallets__flask-5014 | gated | 1 | 28 | 84.1 | 6 | 6 | 1 | 2803 | graded |
| mwaskom__seaborn-3069 | gated | 1 | 60 | 461.2 | 3 | 4 | 0 | 4805 | graded |
| psf__requests-2317 | gated | 0 | 27 | 181.3 | 4 | 6 | 1 | 4524 | graded |
| psf__requests-2317 | baseline | 0 | 37 | 527.9 | 0 |  | 0 | 1356 | graded |
| pydata__xarray-3993 | gated | 0 | 34 | 144.7 | 6 | 6 | 1 | 4021 | graded |
| pylint-dev__pylint-4970 | baseline | 1 | 60 | 607.8 | 0 |  | 0 | 2620 | graded |
| pydata__xarray-3993 | baseline | 1 | 60 | 1016.8 | 0 |  | 0 | 4967 | graded |
| pylint-dev__pylint-4970 | gated | 1 | 60 | 304.6 | 4 | 3 | 0 | 1959 | graded |
| pytest-dev__pytest-7205 | baseline | 1 | 45 | 267.3 | 0 |  | 0 | 2968 | graded |
| pytest-dev__pytest-7205 | gated | 1 | 49 | 353.7 | 4 | 6 | 1 | 3955 | graded |
| scikit-learn__scikit-learn-15100 | baseline | 1 | 32 | 161.5 | 0 |  | 0 | 1215 | graded |
| scikit-learn__scikit-learn-15100 | gated | 1 | 30 | 116.3 | 6 | 6 | 1 | 3334 | graded |
| scikit-learn__scikit-learn-25102 | baseline | 1 | 55 | 493.5 | 0 |  | 0 | 9420 | graded |
| sphinx-doc__sphinx-8593 | baseline | 1 | 41 | 197.8 | 0 |  | 0 | 5941 | graded |
| scikit-learn__scikit-learn-25102 | gated | 1 | 60 | 662.7 | 1 | 5 | 0 | 6442 | graded |

### 正式批原始墙钟成本与异常（仅核对）

| 部分 | 模式 | 指标 | n | 中位数 | P90 |
|------|------|------|--:|-------:|----:|
| 独立部分 | baseline | turns | 13 | 45.0 | 60.0 |
| 独立部分 | baseline | seconds | 13 | 402.2 | 948.6 |
| 独立部分 | baseline | gate_intercepts | 13 | 0.0 | 0.0 |
| 独立部分 | baseline | diff_chars | 13 | 2620.0 | 8834.2 |
| 独立部分 | gated | turns | 13 | 58.0 | 60.0 |
| 独立部分 | gated | seconds | 13 | 366.3 | 858.0 |
| 独立部分 | gated | gate_intercepts | 13 | 4.0 | 8.8 |
| 独立部分 | gated | diff_chars | 13 | 5153.0 | 11560.6 |
| 复用部分（单列） | baseline | turns | 2 | 33.0 | 34.6 |
| 复用部分（单列） | baseline | seconds | 2 | 559.8 | 898.4 |
| 复用部分（单列） | baseline | gate_intercepts | 2 | 0.0 | 0.0 |
| 复用部分（单列） | baseline | diff_chars | 2 | 1545.0 | 2101.0 |
| 复用部分（单列） | gated | turns | 2 | 44.0 | 56.8 |
| 复用部分（单列） | gated | seconds | 2 | 272.6 | 423.5 |
| 复用部分（单列） | gated | gate_intercepts | 2 | 4.5 | 5.7 |
| 复用部分（单列） | gated | diff_chars | 2 | 3804.0 | 4604.8 |

- note 分布：`{"graded": 31}`。
- 零拦截 gated：`[]`。
- 超过 60 分钟：`["matplotlib__matplotlib-24970/baseline"]`。
- 至少 100,000 字符的补丁：`[]`。
- 官方 resolved 但门禁未完成：`["django__django-14631", "django__django-16333", "django__django-16901", "matplotlib__matplotlib-24970", "mwaskom__seaborn-3069", "pylint-dev__pylint-4970", "scikit-learn__scikit-learn-25102"]`。
- 审计计数或状态异常：`[]`。
- 门禁完成但官方 unresolved 的待审查实例：`["psf__requests-2317", "pydata__xarray-3993"]`。
- 未合并三批成功率或检验；尚未人工复核的异常不作机制有效性结论。
