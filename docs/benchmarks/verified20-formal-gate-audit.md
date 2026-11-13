# 正式批门禁与官方评分差异：证据审计

2026-11-13，已完成原产物核对和两项独立容器重放，不等待全批结束。
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
不在当前批运行期间静默修改门禁。两类问题的其他实例受影响范围尚未审完；未发现 C 类具体违例。

## 收尾次序与结论边界

上述两项目标重放和 Seaborn 对照已提前完成，见 [Seaborn 对照](verified20-seaborn-comparison.md)。
全批结束后先核验 40 个预期 instance/mode 的官方证据，再检查新增异常是否均已审计，
并与 Astropy 已审计历史作对照；不能将当前两项核查当作剩余实例的批量豁免。
自动表格可更新，存在待归类项时保持机制审查未定稿。
后续新增异常加入同一汇总表，不因该二元组合停止跑批。
本批复用为 2 个实例、4 项任务；前两批 4 个重叠实例的 8 项模式比较另行核对。

最终核对按以下清单执行，当前跑批尚未完成，不预勾完成：

1. 40 个预期 instance/mode 唯一键齐全，逐项 patch、stats、prediction、官方 report 可验证；不能只数 CSV 行。
2. Seaborn/Flask 两个实例的 baseline/gated 共 4 项齐全；不使用错误的“应有 8 项”验收条件。
3. 本次恢复之后的 `completion_verification.json` 为 passed，40/40 已核验，不仅是文件存在。
4. 三批身份去重 55−4−2=49，仓库与实际执行数量按最终证据填，去重数不冒充完全独立观测。
5. 正式独立 18 配对、复用 2 配对分开；pilot 及诊断重放不进入正式统计。
6. 成本基于全 40 项证据重算，独立/复用分开，原始墙钟与排除跨中断口径并列；时间 n 为 baseline 17、gated 18。
7. Requests、Xarray、Astropy 的运行身份和现有审计保留，新增反向组合及零拦截、large_patch、长耗时按证据复核。
8. Seaborn 对照保留已定位的显式 y 范围差异，不能简化为“截断导致失败”。
9. 交集按实际实例计算：链路 pilot-5→正式 5 个；前两批原 4 个重叠与正式交集为 0；缓存-15→正式复用 2 个。缓存批 Seaborn pilot 单独对照。
10. 局限保留样本与依赖、复用、无 chroot、缓存偏差、门禁≠官方、禁止 pooled、扩样不保证显著、P90 敏感、测试演进缺少覆盖不减约束。
11. 最终 diff 核对自动表格与手工审计无冲突；有 baseline-only 如实报告，存在未审机制项不宣称审计定稿。
