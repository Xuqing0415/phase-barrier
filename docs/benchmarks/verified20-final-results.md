## 正式结果

已完成 40 项官方评分、20/20 个完整配对，评分覆盖 12 个仓库。仅双组均 graded 的实例进入配对表；缺失不当作 unresolved。

| 批次 / 部分 | 配对数 | baseline resolved | gated resolved | baseline-only | gated-only |
|---|---:|---:|---:|---:|---:|
| Lite Scale-20 | 20 | 18 | 20 | 0 | 2 |
| Verified 缓存-15 | 15 | 12 | 13 | 0 | 1 |
| 独立部分 | 18 | 16 | 15 | 1 | 0 |
| 复用部分 | 2 | 2 | 2 | 0 | 0 |

### 三批实际去重

| 批次 | 完整配对实例数 | 跨批重叠 | 新增不同实例 |
|---|---:|---:|---:|
| Lite Scale-20 | 20 | 0 | 20 |
| Verified 缓存-15 | 15 | 4 | 11 |
| Verified 预注册-20 | 20 | 2 | 18 |

累计 49 个不同实例；去重计数不保证观测相互独立。

### 正式成本

秒数主表排除跨中断耗时；轮数、拦截和补丁字符仍保留该实例。

| 部分 | 模式 | 指标 | 口径 | n | 中位数 | P90 |
|---|---|---|---|---:|---:|---:|
| 独立部分 | baseline | turns | 全部实例 | 18 | 43.0 | 60.0 |
| 独立部分 | baseline | seconds | 排除跨中断耗时 | 16 | 375.4 | 641.8 |
| 独立部分 | baseline | gate_intercepts | 全部实例 | 18 | 0.0 | 0.0 |
| 独立部分 | baseline | diff_chars | 全部实例 | 18 | 2448.5 | 7369.7 |
| 独立部分 | gated | turns | 全部实例 | 18 | 51.0 | 60.0 |
| 独立部分 | gated | seconds | 排除跨中断耗时 | 17 | 304.6 | 767.1 |
| 独立部分 | gated | gate_intercepts | 全部实例 | 18 | 4.0 | 8.3 |
| 独立部分 | gated | diff_chars | 全部实例 | 18 | 3988.0 | 11077.1 |
| 复用部分 | baseline | turns | 全部实例 | 2 | 33.0 | 34.6 |
| 复用部分 | baseline | seconds | 排除跨中断耗时 | 2 | 559.8 | 898.4 |
| 复用部分 | baseline | gate_intercepts | 全部实例 | 2 | 0.0 | 0.0 |
| 复用部分 | baseline | diff_chars | 全部实例 | 2 | 1545.0 | 2101.0 |
| 复用部分 | gated | turns | 全部实例 | 2 | 44.0 | 56.8 |
| 复用部分 | gated | seconds | 排除跨中断耗时 | 2 | 272.6 | 423.5 |
| 复用部分 | gated | gate_intercepts | 全部实例 | 2 | 4.5 | 5.7 |
| 复用部分 | gated | diff_chars | 全部实例 | 2 | 3804.0 | 4604.8 |

matplotlib-24970 baseline 的 220,038.7 秒跨两次中断；SymPy-18211 baseline/gated 的 9,070.8/8,814.0 秒跨宿主机睡眠。三项均排除出主要耗时统计，原始结果、轮数和拦截数保留。
宿主机在 2026-11-13 11:59:40–14:22:42（北京时间）睡眠，Windows Power-Troubleshooter 事件 1（record 51905）与两组 run/grade 边界相符。证据见 [机制审计](verified20-formal-gate-audit.md)。
不从原始墙钟中估算扣减睡眠时间；以下敏感性表保留完整原始墙钟，供核对排除影响。

| 部分 | 口径 | baseline n | baseline P90 秒 | gated n | gated P90 秒 |
|---|---|---:|---:|---:|---:|
| 独立部分 | 全部实例（原始墙钟） | 18 | 3433.0 | 18 | 1098.7 |
| 独立部分 | 排除跨中断耗时 | 16 | 641.8 | 17 | 767.1 |
| 复用部分 | 全部实例（原始墙钟） | 2 | 898.4 | 2 | 423.5 |
| 复用部分 | 排除跨中断耗时 | 2 | 898.4 | 2 | 423.5 |

复用部分：排除跨中断耗时后，gated P90 为 baseline 的 0.47×（423.5s vs 898.4s；gated n=2、baseline n=2）。
独立部分：排除跨中断耗时后，gated P90 为 baseline 的 1.20×（767.1s vs 641.8s；gated n=17、baseline n=16）。
这是两组 P90 的描述性比值，不是逐实例平均减速，也不能隔离门禁、测试和模型重试各自的耗时贡献。部分结果不能证明总体解决率不降低；最终结论须同时报告配对解决率和成本。
最终成本结论须基于全部 40 项核验证据，独立与复用分开；全量独立配对 n=18，排除跨中断耗时后 baseline 时间 n=16、gated n=17。P90 对离群值敏感，样本量有限；同一正式批的中途估计曾随样本增加由 2.16× 漂移至 1.87×，这些中间数字及最终比值均为点估计，不是精确值或置信区间。

### 成本来源分段

来源按首次中断归档 CSV 中已评分的 instance+mode 划分。当前评分数：`{"中断前已评分": 9, "恢复后评分（含补评）": 31}`。
全量时为中断前 9 项、恢复后 31 项评分。补评分沿用原 Agent 的轮数和耗时，因此“恢复后评分”不等于 Agent 全程使用新规则，也不证明环境稳定；不将其直接称为稳定环境下的成本。
分段成本仍只取双组评分齐全的实例，秒数排除跨中断耗时；每行注明实际 n。

| 部分 | 评分来源 | 模式 | 指标 | n | 中位数 | P90 |
|---|---|---|---|---:|---:|---:|
| 独立部分 | 中断前已评分 | baseline | turns | 5 | 37.0 | 59.6 |
| 独立部分 | 中断前已评分 | baseline | seconds | 5 | 301.7 | 379.8 |
| 独立部分 | 中断前已评分 | baseline | gate_intercepts | 5 | 0.0 | 0.0 |
| 独立部分 | 中断前已评分 | baseline | diff_chars | 5 | 5495.0 | 11034.8 |
| 独立部分 | 恢复后评分（含补评） | baseline | turns | 13 | 45.0 | 60.0 |
| 独立部分 | 恢复后评分（含补评） | baseline | seconds | 11 | 493.5 | 675.7 |
| 独立部分 | 恢复后评分（含补评） | baseline | gate_intercepts | 13 | 0.0 | 0.0 |
| 独立部分 | 恢复后评分（含补评） | baseline | diff_chars | 13 | 2277.0 | 5746.2 |
| 独立部分 | 中断前已评分 | gated | turns | 4 | 60.0 | 60.0 |
| 独立部分 | 中断前已评分 | gated | seconds | 4 | 516.5 | 652.8 |
| 独立部分 | 中断前已评分 | gated | gate_intercepts | 4 | 3.5 | 6.8 |
| 独立部分 | 中断前已评分 | gated | diff_chars | 4 | 11270.5 | 13247.1 |
| 独立部分 | 恢复后评分（含补评） | gated | turns | 14 | 47.5 | 60.0 |
| 独立部分 | 恢复后评分（含补评） | gated | seconds | 13 | 272.8 | 855.3 |
| 独立部分 | 恢复后评分（含补评） | gated | gate_intercepts | 14 | 4.5 | 8.7 |
| 独立部分 | 恢复后评分（含补评） | gated | diff_chars | 14 | 2838.0 | 5783.7 |
| 复用部分 | 恢复后评分（含补评） | baseline | turns | 2 | 33.0 | 34.6 |
| 复用部分 | 恢复后评分（含补评） | baseline | seconds | 2 | 559.8 | 898.4 |
| 复用部分 | 恢复后评分（含补评） | baseline | gate_intercepts | 2 | 0.0 | 0.0 |
| 复用部分 | 恢复后评分（含补评） | baseline | diff_chars | 2 | 1545.0 | 2101.0 |
| 复用部分 | 恢复后评分（含补评） | gated | turns | 2 | 44.0 | 56.8 |
| 复用部分 | 恢复后评分（含补评） | gated | seconds | 2 | 272.6 | 423.5 |
| 复用部分 | 恢复后评分（含补评） | gated | gate_intercepts | 2 | 4.5 | 5.7 |
| 复用部分 | 恢复后评分（含补评） | gated | diff_chars | 2 | 3804.0 | 4604.8 |

### 重叠实例跨批一致率

| 比较 | 重叠实例 | 已比较 | 一致 | 不一致 |
|---|---:|---:|---:|---|
| 链路 pilot-5 → 正式批 | 5 | 5 | 4 | astropy__astropy-14369 |
| 前两批共有实例 → 正式批 | 0 | 0 | 0 | — |
| 缓存-15 → 正式批（复用） | 2 | 2 | 2 | — |

一致指同一实例 baseline/gated 两个二元结果均一致。前两批相互重叠的 4 个实例与本批的交集按实际 ID 计算。
逐项核对：前两批的 8 个重叠 instance+mode 比较均有记录；本批跨批复用 4 项任务，已 graded 4 项。两类键清单分别保存在汇总 JSON 的 `historical_overlap_jobs` 和 `formal_reuse_jobs`，不能把前者的 8 项当作本批有 8 项复用任务。

### 两个复用实例的三次结果

| 实例 | 模式 | 缓存批 pilot | 缓存-15 | 正式批 | 运行间变异 |
|---|---|---|---|---|---|
| mwaskom__seaborn-3069 | baseline | resolved | resolved | resolved | 未观察到 |
| mwaskom__seaborn-3069 | gated | unresolved | resolved | resolved | 有 |
| pallets__flask-5014 | baseline | resolved | resolved | resolved | 未观察到 |
| pallets__flask-5014 | gated | resolved | resolved | resolved | 未观察到 |

此表缓存批 pilot 使用 `.pytest_tmp/verified15/pilot5_results.csv`，与链路 pilot-5 是不同运行。Flask 另有单实例 pilot 的 54 轮记录，未混入此表的缓存批 pilot-5（15 轮）。结果差异记录为运行间变异；代码与环境存在变化，不直接归因于门禁或模型采样。

### 异常实例

| 实例 | 模式 | 标记 |
|---|---|---|
| django__django-14631 | gated | official_resolved_gate_incomplete, budget_exhausted_at_late_stage |
| django__django-16333 | gated | official_resolved_gate_incomplete, budget_exhausted_at_late_stage |
| django__django-16901 | gated | official_resolved_gate_incomplete, budget_exhausted_at_late_stage |
| matplotlib__matplotlib-24970 | gated | official_resolved_gate_incomplete, budget_exhausted_at_late_stage |
| matplotlib__matplotlib-24970 | baseline | over_60_minutes, cross_interruption_wall_time_excluded_from_efficiency, cross_interruption_excluded_from_efficiency |
| mwaskom__seaborn-3069 | gated | official_resolved_gate_incomplete, budget_exhausted_at_late_stage |
| psf__requests-2317 | gated | gate_complete_official_unresolved, env_tls_interference |
| pydata__xarray-3993 | gated | gate_complete_official_unresolved, A_warning_mismatch+B_test_assertion_weakened |
| pylint-dev__pylint-4970 | gated | official_resolved_gate_incomplete, early_stage_incomplete |
| scikit-learn__scikit-learn-25102 | gated | official_resolved_gate_incomplete, budget_exhausted_at_late_stage |
| sphinx-doc__sphinx-9281 | gated | official_resolved_gate_incomplete, budget_exhausted_at_late_stage |
| sympy__sympy-13480 | gated | zero_interception_suspicious |
| sympy__sympy-17630 | gated | zero_interception_suspicious |
| sympy__sympy-18211 | gated | over_60_minutes, cross_interruption_wall_time_excluded_from_efficiency, zero_interception_suspicious, cross_interruption_excluded_from_efficiency |
| sympy__sympy-18211 | baseline | over_60_minutes, cross_interruption_wall_time_excluded_from_efficiency, cross_interruption_excluded_from_efficiency |

正式异常已逐项复核：门禁完成但官方未通过 2 项、官方通过但门禁未完成 8 项、零拦截 3 项、SymPy 超 60 分钟 2 项；Matplotlib baseline 跨中断单列。
四类合计 15 次类别归属、14 个不同任务（SymPy-18211 gated 重叠），加上 Matplotlib baseline 后异常实例表共 15 行。
零拦截三项均归为 `zero_interception_suspicious`：阶段 2 的 `cd /testbed && python -m pytest ...` 未被命令识别器识别；stdout 工具调用与 trace 的轮次/工具逐项一致，系命令漏识别，未发现拒绝事件漏写证据。
8 个门禁未完成且官方 resolved 的实例：7 个在阶段 4/5 耗尽或接近轮数预算，Pylint 在阶段 3 因存量负向语法夹具阻断。详见 [完整归类与检查清单](verified20-formal-gate-audit.md)。

### 门禁完成但官方未通过：正式批 2 项

此处只计正式批 gated，不把 Astropy pilot 的 A 类混入正式批计数；Astropy 正式批双组 resolved。
| 正式实例 | 归类 | 待核验依据 |
|---|---|---|
| psf__requests-2317 | env_tls_interference（环境混杂） | 原产物及同补丁独立容器重放已核验，详见统一审计记录 |
| pydata__xarray-3993 | A 类警告断言差异 + B 类 test_assertion_weakened | 原产物及同补丁独立容器重放已核验，详见统一审计记录 |

A 为测试覆盖不足；B 为测试篡改；C 为 delivery_clean 缺陷。仅凭两个结果标记不能判定归类，也不能断言门禁通过是官方 resolved 的必要条件；官方 resolved 且门禁未完成的观察另列。
[统一审计记录](verified20-formal-gate-audit.md) 保留 Requests 环境混杂与 Xarray 的 A/B 两项证据。
[Seaborn 专项对照](verified20-seaborn-comparison.md) 已核对缓存批 pilot 与正式批的补丁及阶段；共同触达 60 轮不能单独解释官方结果差异。

Django gated 的 4 项运行依次为 django__django-14631：60 轮、阶段 4、gate_completed=0；django__django-16333：60 轮、阶段 4、gate_completed=0；django__django-16631：60 轮、阶段 5、gate_completed=0；django__django-16901：53 轮、阶段 5、gate_completed=0。官方结果为 3 resolved、1 unresolved。
这些运行普遍触达或接近 60 轮上限，停在阶段 4/5。门禁未完成不能直接解读为官方评分受阻；3 个实例官方 resolved，可作为门禁未必阻断官方评分的观察，不作为门禁全流程有效的证据。这一模式与轮数预算和多阶段开销的交互相容；没有增加轮数预算的对照实验，尚不能确定因果。

astropy__astropy-14369 gated：37 轮、4 次拦截、阶段 6、gate_completed=1、13,887 字符。

matplotlib__matplotlib-23476 gated：58 轮、8 次拦截、阶段 6、gate_completed=1、1,328 字符。

正式批最早完成全流程的是 Astropy-14369；Matplotlib-23476 是另一个接近 60 轮上限仍完成的实例。它表明该预算下存在完成全流程的案例，60 轮不是绝对阻断，但不能据此确定 Django 未完成的原因。

机制审查与二元评分分别报告；自动统计不替代原产物核对和独立重放。

## 局限与结论

- 全量三批涉及 49 个不同实例（名义 n=49），不保证观测独立，不足以宣称 p<0.05。
- 本批 2 个复用实例非完全独立。
- 缓存-15 存在选择偏差。
- 容器无 chroot，Agent 可读门禁源码。
- 门禁通过不等于官方通过，保留 Astropy A 类历史案例。
- 三批不合并 pooled 成功率或显著性检验；pilot 及审计复核不进入正式统计。
- 运行间代码、环境与中断会影响结果及耗时，不能将观察到的差异单独归因于门禁。
- Xarray 重放证实阶段 2 的关键自写断言在阶段 3 被替换，最终测试通过而原登记断言失败；当前门禁未保证测试演进保留既定行为覆盖。证据与 A/B 两项解释见 [统一审计](verified20-formal-gate-audit.md)，不单独归因于官方 warning 失败。
- 成本 P90 样本量有限且对离群值敏感；2.16×→1.87× 是同一正式批随样本增加的中途漂移。全部 40 项证据齐全后重算，独立配对 n=18，排除跨中断耗时后的时间 n 为 baseline 16、gated 17；此前只排除 Matplotlib 得到的 1.35× 已被宿主机睡眠证据修正，最终比值见成本表，均为点估计。
- 扩展到 100+ 实例仅为规划，不能保证显著性；所需样本量取决于效应及配对不一致率。
- 8 个门禁未完成且官方 resolved 的实例不构成门禁全流程有效的证据；预算与门禁开销未做对照。另发现复合 shell 测试命令漏识别，以及 Pylint 存量负向语法夹具误阻断，均保留为待修复机制缺口。

出现 baseline-only 的批次/部分：独立部分。需结合逐实例结果如实分析，不能宣称三批均未出现退步。
