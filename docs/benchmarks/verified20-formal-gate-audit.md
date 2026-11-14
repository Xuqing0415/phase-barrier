# 正式批门禁与官方评分差异：证据审计

2026-11-14 完成正式批异常机制复核。40 项官方证据已重新逐项核验，20/20 配对完整。
2026-11-13 已完成下述两项独立容器重放；2026-11-14 补查三个正式 SymPy、八个反向组合和宿主机睡眠。
模板见 [共用模板](verified20-gate-audit-template.md)。两次诊断均使用原 LogicStar 镜像 ID、
原 base commit 和原 gated 补丁；先运行自写测试，再应用评分器原 test patch 并运行目标官方测试。
未调用模型、未关闭 TLS 校验、未改正式证据或运行中的门禁，也未覆盖原官方评分。
诊断容器内未重新 pip 安装依赖，使用镜像已有 testbed 环境和 `/testbed` 下的补丁源码；
这不是完整评分器复跑，不能把诊断结果当作新增正式观测。

## 汇总

| 实例 | 运行 | 官方 F2P / P2P | 门禁 | 初步判定 |
|---|---|---|---|---|
| requests-2317 | 正式 gated | 7/8；127/133 | 完成，阶段 6 | `env_tls_interference`，环境混杂，单列于 A/B/C 之外 |
| xarray-3993 | 正式 gated | 0/2；2398/2398 | 完成，阶段 6 | A 类警告断言差异与 B 类 `test_assertion_weakened` 分开记录，不给单一归类 |
| astropy-14369 | pilot gated | 2/3；730/732 | 完成，阶段 6 | 已归 A；旧状态缺失的局限保留 |
| astropy-14369 | 正式 baseline / gated | 两组均 3/3；732/732 | gated 完成，阶段 6 | 两组 resolved，不是正式批该异常组合 |

当前正式批该组合为 2 个，不是 3 个；不得写成“三个均为 A 类”。
正式异常计数：门禁完成且官方 unresolved **2**；门禁未完成且官方 resolved **8**；零拦截 **3**；
SymPy-18211 超 60 分钟 **2**（baseline/gated），Matplotlib-24970 baseline 跨中断另列。
四类有 15 次类别归属、14 个不同任务（SymPy-18211 gated 同时属于零拦截和长耗时），
加 Matplotlib baseline 后异常表为 **15 个不同 instance/mode、15 行**，不能把类别相加当作唯一任务数。
Astropy 的既有同补丁重放见 [专项审计](verified20-astropy-audit.md)。
本次两项证据及其 SHA256 清单保存在
`.pytest_tmp/verified20/formal20_logicstar/formal_gate_preliminary_audit.json`。
追加的重放和 Seaborn 对照证据见 `formal_parallel_audit_evidence.json`；原初查清单保留。
实际产物分在 `agent_runs/<label>/`、`eval_runs/reports/<label>.json` 和
`eval_runs/logs/run_evaluation/<label>-<run-id>/phase-barrier/<instance_id>/`，
不使用不存在的 `<instance>/gated/report.json` 路径。

## Requests：psf__requests-2317（正式 gated）

- 门禁：完成，阶段 6，27 轮，4,524 字符，4 次拦截；32 条 trace 事件。
- 官方：unresolved，F2P 7/8、P2P 127/133，补丁成功应用，`infra_failure=false`，`note=graded`。
- 归类：**`env_tls_interference`；环境混杂，不归入 A/B/C。**
- 补丁只修改 `requests/models.py`、`requests/sessions.py`，新增 `test_pb_repro.py`。
  未修改既有 `test_requests.py` 或 `tests/`；没有最终补丁层面的官方测试篡改证据。
- 自写测试：原运行第 26 轮 `pytest test_pb_repro.py -q`，6 passed。
  阶段 2 写入版本与最终补丁内版本 SHA256 相同，未见自写测试版本变化。
- F2P 唯一失败为 `test_requests.py::RequestsTestCase::test_requests_history_is_saved`；
  原日志 2263 行开始该节，2774 行为 `SSLCertVerificationError`：无法获取本地颁发者证书。
  本次修复对应的 `TestTimeout::test_encoded_methods` 实际已在官方 F2P 中通过。
- P2P 的 6 个失败涉及认证、重定向、Unicode header 与 stream timeout；日志可见
  TLS 证书错误及远端断连。自写测试使用 `RecordingAdapter`，不调用外部服务，
  没有复现该 TLS/重定向环境路径。遗漏路径属覆盖事实，但不足以把环境失败定性为补丁能力问题。
- 时间戳：最后变更 `1794537045.633549`，最后测试 `1794537137.8095784`，
  交付 `1794537139.7465427`。状态与 trace 连续性、4 次 blocked 计数核验通过；
  对保存状态只读执行现有 `delivery_clean()` 返回 true，未发现时间新鲜度违例。
- 结论与统计：保留原 unresolved，单列环境干扰；不作为门禁有效或有害的机制证据。
  `infra_failure=false` 是评分器原字段，不等于测试依赖的外部环境没有故障。

三步核验已完成：baseline 原评分的同一 `test_requests_history_is_saved` 也报证书错误
（原日志 2331 行）；同一独立容器内 gated 自写测试 6/6 通过；
该官方失败测试连续两次均为 1 failed，并复现 `CERTIFICATE_VERIFY_FAILED`。
诊断输出在 `.pytest_tmp/verified20/formal_audit_replays/requests_20261113T030444Z/`，
镜像 ID 为 `sha256:d8188f303b2beaaf7b066a84eea1cb710bd1879fb5cc5f764e70a954fe4997c8`。
两次重放不是所有网络条件下“必然失败”的证明，也未定位到具体 CA/网络代理来源，
但 baseline 与 gated 的共同证据不支持将该失败归因于 gated 补丁的测试覆盖。
保留原 `graded` / unresolved，不将测试环境干扰擅自改写为 `grade_infra_failure`，
不追加通过优先的重试或改变当前停止规则。

## Xarray：pydata__xarray-3993（正式 gated）

- 门禁：完成，阶段 6，34 轮，4,021 字符，6 次拦截；38 条 trace 事件。
- 官方：unresolved，F2P 0/2、P2P 2398/2398；补丁成功应用，`infra_failure=false`，`note=graded`。
- 归类：**A 类行为断言差异 + B 类 `test_assertion_weakened`；两项独立记录，不给最终单一归类。**
- 补丁修改 `xarray/core/dataarray.py`、`doc/whats-new.rst`，新增 `test_pb_repro.py`，
  未修改既有 `xarray/tests/`。因此不存在最终补丁直接修改官方测试的证据。
- 两个官方 F2P 是 `xarray/tests/test_dataset.py::test_integrate[True]` 和
  `xarray/tests/test_dataset.py::test_integrate[False]`。日志 1207–1208 和 1268–1269 行：
  官方要求 `FutureWarning`，实际只发出 `DeprecationWarning`；自写
  `test_integrate_dim_deprecated` 则要求 `DeprecationWarning`，未断言官方要求的警告类型。
  这是可定位的行为要求差异，不是泛称“隐藏场景没覆盖”。
- 原运行第 33 轮 `pytest test_pb_repro.py` 为 3 passed；独立容器同补丁重放也为 3 passed。
  完整原测试日志另有 Pandas/Xarray 版本兼容及 warning 数量失败，均不在官方 P2P 失败清单中，
  不把所有原始 pytest 失败等同于官方 F2P/P2P 失败。
- **独立的 B 类审查项**：第 7 轮、阶段 2 的自写测试含
  `test_integrate_coord_and_dim_raises`，第 11 轮、阶段 3 重写同一文件，
  替换成 `test_integrate_dataset_and_dataarray_consistent`。
  阶段历史仍保留原测试名与原哈希，最终实现没有处理同时传 `coord` 与 `dim` 的报错分支。
  重放阶段 2 原始测试得到 2 passed、1 failed，失败项为该报错断言，实际 DID NOT RAISE TypeError；
  替换测试只比较 Dataset/DataArray 数值，不覆盖同时传 `coord` 和 `dim` 的行为，非等价改名。
  因此该维度确有断言覆盖弱化，标记 `test_assertion_weakened`。
  这不证明主观作弊，也不解释官方 warning 失败；后者与自写测试的错误警告类型要求是另一问题。
- 时间戳：最后变更（自写测试）`1794537549.860127`，最后测试 `1794537656.5777566`，
  交付 `1794537658.3267846`。计数及阶段连续性核验通过；保存状态上的
  `delivery_clean()` 返回 true，现存证据未显示测试之后改码仍被交付的 C 类违例。
- 轨迹还记录了阶段 4 通过 shell 读取 `/pb_runs/.../trace.jsonl` 等产物，
  不把该容器当作审计文件不可见的隔离环境；仅凭可读操作不推断状态被篡改。
- 结论与统计：保留原 unresolved，单列；尚不能下“这不是门禁 bug”的最终结论。
  正式 baseline 为 resolved，因此这是本批一个 baseline-only 配对；原二元结果如实进入正式表，
  不能因已知门禁设计边界或后续修复而删除该差异，也不把单个配对差异当作总体因果效应。

独立容器重放已完成：最终自写 3/3 通过，原始登记版本 2/3 通过，两个官方 F2P 均复现
“要求 FutureWarning、仅发出 DeprecationWarning”。输出在
`.pytest_tmp/verified20/formal_audit_replays/xarray_20261113T030514Z/`，镜像 ID 为
`sha256:0da56808081977656206303dd832d02a08e42a0f623c70d724292e11ee18aada`。
源码核对：`validate_tests()` 保存测试哈希与测试名；阶段 3 的 `validate_implementation()`
检查实现语法，`delivery_clean()` 检查最新测试结果和变更时间，未保证阶段 2 的行为断言仍被保留。
Xarray 暴露“允许测试演进但未强制保持或增强既定断言覆盖”的门禁设计边界，列入后续改进，
原正式批没有因诊断结论静默修改门禁；评分和原始证据保留。未发现 C 类具体违例。

## 正式批零拦截：三个均为 suspicious，已定位机制

本节逐一核对正式运行，未复用 pilot 的 benign 结论。阶段历史记录的是已完成阶段，
末尾的 current_stage=6 需与 trace 合并解读；4→6 是合法转换。

| 正式 gated 实例 | 阶段轨迹 | trace 事件 / blocked | 补丁字符（字节） | 归类 | 依据 |
|---|---|---:|---:|---|---|
| sympy-13480 | 0→1→2→3→4→6 | 23 / 0 | 1,954（1,954） | zero_interception_suspicious | 第 10 轮阶段 2 执行复合 pytest 命令，blocked=false |
| sympy-17630 | 0→1→2→3→4→6 | 69 / 0 | 1,968（1,968） | zero_interception_suspicious | 第 39 轮阶段 2 执行复合 pytest 命令，blocked=false |
| sympy-18211 | 0→1→2→3→4→6 | 33 / 0 | 2,342（2,342） | zero_interception_suspicious | 第 10 轮阶段 2 执行复合 pytest 命令，blocked=false |

三项都包含 `cd /testbed && python -m pytest test_pb_repro.py ...`。现有
`check_exec_permission()` 规定阶段 <3 拒绝已识别的测试命令，但 Python/通用命令
识别器未识别 cd 前缀后的 pytest；原调用被执行，并非“按 SOP 首次即通过”。
stdout 的 `(turn, tool)` 序列与 trace 逐项一致，23/69/33 条均完整；
trace、stats 均为 0 blocked，未发现“拒绝发生但日志漏写”证据。结论是
**`shell_test_command_not_recognized`，命令漏识别，而非计数器漏记**。
补丁均包含目标源码修改和新增复现测试，未见 <200 字符的表面补丁；完整阶段轨迹不消除该已观察到的漏拦截。
修复及回归纳入后续门禁修复阶段，不改写本轮原始拦截数。

## 官方 resolved 但门禁未完成：按模式归类

| 模式 | 实例与最终阶段 / 轮数 | 数量 | 归类依据 |
|---|---|---:|---|
| 后期预算耗尽或接近上限 | Django-14631（4/60）、16333（4/60）、16901（5/53）；Matplotlib-24970（5/60）；Seaborn-3069（4/60）；scikit-learn-25102（5/60）；Sphinx-9281（4/60） | 7 | budget_exhausted_at_late_stage；Django-16901 另有历史模型错误，53 轮只是接近上限，不声称必然由轮数预算停止 |
| 早期阶段未完成 | Pylint-4970（3/60） | 1 | early_stage_incomplete；测试阶段已经通过，在实现校验被拒绝 |
| 其他 / 未归类 | 无 | 0 | 没有阶段 6 且 gate_completed=0 的实例 |

Pylint 的已完成历史为 0→1→2，current_stage=3，不能写成“测试没写完”。
第 24、30 轮的 3→4 推进都被拒绝：存量 `tests/functional/a/async_functions.py:57`
的负向语法夹具包含重复参数，项目全量实现语法扫描把它视为实现错误。
该文件不是最终补丁修改对象，Agent 后续多轮查找门禁源码及配置并耗尽 60 轮；
这与 Django 的阶段 4/5 模式不同，揭示存量夹具误分类边界。
Sphinx 的三次 4→5 拒绝为未识别测试运行记录，不能只将这一模式解释为 Agent 没有运行测试。

所有八项的官方 resolved 均不证明门禁全流程有效。预算、多阶段开销和命令识别的交互尚未做对照；
`budget_exhausted_at_late_stage` 是轨迹描述，不是门禁造成失败的因果结论。
轨迹还显示部分 Agent 在线获取上游版本或 PR 内容，本轮没有禁止网络检索，
因此不把这些运行解释为排除了上游答案接触的独立求解实验。

## 超 60 分钟：SymPy 双组跨宿主机睡眠

Windows `Microsoft-Windows-Power-Troubleshooter` 事件 1、record 51905 记录：
睡眠 **2026-11-13 11:59:40.6427993**，唤醒 **14:22:42.3183628**（北京时间）。
脱敏事件字段已保存到 `benchmarks/swebench/results/verified20_host_sleep_events.json`。

| 实例 / 模式 | runner run→grade（北京时间，2026-11-13） | 原始秒数 | 结论 |
|---|---|---:|---|
| sympy-18211 baseline | 11:55:07→14:26:23 | 9,070.8 | cross_interruption；主要耗时统计排除 |
| sympy-18211 gated | 11:58:27→14:25:25 | 8,814.0 | cross_interruption；主要耗时统计排除 |

gated 阶段 1→2 的完成时间为 11:59:22→14:23:11，亦跨越同一睡眠区间。
长耗时不应解释为真实连续模型执行。原 stats 没有 started_at/finished_at，故采用
runner 边界、state 阶段时间及系统事件交叉核对；不从墙钟估算扣减睡眠时长。
与既有 Matplotlib baseline 一起，正式主成本时间样本修正为 **baseline n=16、gated n=17**；
轮数、拦截、补丁及全部 18 个独立配对结果仍保留。
此前 n=17/18、P90 比值 1.35× 是发现宿主机睡眠前的口径，已由重新生成的成本表替代。

复核脚本：`python scripts/verified20_anomaly_audit.py --output benchmarks/swebench/results/verified20_anomaly_audit.json`。
该产物保存原文件相对路径及 SHA-256、每项归类、日志/trace 对账和系统事件；脚本不执行历史命令。

## 收尾次序与结论边界

两项目标重放和 Seaborn 对照已完成，见 [Seaborn 对照](verified20-seaborn-comparison.md)。
2026-11-14 重新核验 40 个官方任务及全部异常后，本轮机制审查定稿；
定稿表示观察和缺陷已归类，不表示上述缺陷已修复或门禁已证实有效。
本批复用为 2 个实例、4 项任务；前两批 4 个重叠实例的 8 项模式比较另行核对。

最终核对（2026-11-14，本地证据与报告；推送和远程 CI 单独验收）：

1. [x] 40 个唯一键的 patch、stats、prediction、官方 report 已逐项验证。
2. [x] Seaborn/Flask 共 4 项；前两批重叠的 8 项模式比较另列。
3. [x] 原 completion_verification passed，当前重新核验 skip=40、其余=0，原证据未修改。
4. [x] 三批 55−4−2=49 个不同实例；正式批实际 20 实例、12 仓库，不冒充相互独立。
5. [x] 独立 18、复用 2；pilot 和诊断不入正式统计。
6. [x] 全 40 项成本重算；依据新发现的睡眠证据，时间 n 修正为 16/17，保留原始墙钟敏感性表。
7. [x] 两个正向异常、八个反向异常、三个零拦截、长耗时均已复核；无十万字符补丁。零拦截三项为命令漏识别，无拒绝事件漏写证据。
8. [x] Seaborn 的显式 y 范围差异与 60 轮截断分开报告。
9. [x] 实际交集分别为 5、0、2；缓存批 Seaborn pilot 单独对照。
10. [x] 最终报告保留十一条局限，增加命令识别、存量夹具及网络检索观察，不宣称样本量保证显著性。
11. [x] 自动结果及成本与人工审计交叉核对，Xarray baseline-only 原样保留，历史快照和最终结论明确区分。
