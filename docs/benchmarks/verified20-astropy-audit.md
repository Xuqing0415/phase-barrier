# Astropy pilot 反向组合审计

实例：`astropy__astropy-14369`。对象为原 pilot gated 补丁，SHA256 前缀
`43227f8f6936`，36 轮、14,618 字符。门禁阶段 6、gate_completed=1；
官方 unresolved，note=graded，patch_successfully_applied=true，infra_failure=false。

## 证据与归类

**归类 A：`gate_completed_insufficient`，测试覆盖不足。** 这是对该次补丁的审查，
不是对其他反向组合的一揽子豁免。

最终补丁只改 `astropy/units/format/cds.py`、生成的 `cds_parsetab.py`，
并新增 `test_pb_repro.py`。未修改既有官方测试文件，不能据此归类 test_tampering。
Agent 新增两个测试函数，参数化后共 4 个用例：三个合法连续除法单位字符串，
以及表格读取时两列的单位断言。没有“混用除号与点乘的非法字符串应抛 ValueError”的断言。

在同一 LogicStar 镜像中重放原补丁（不调用模型、不应用官方 test patch）：

- `python -m pytest -q test_pb_repro.py`：4 passed。
- `python -m pytest -q astropy/units/tests/test_format.py -k test_cds_grammar_fail`：20 passed。

官方 test patch 后的原始评分日志则报告：

- `test_cds_grammar_fail[km/s.Mpc-1]`：DID NOT RAISE ValueError，属于 FAIL_TO_PASS。
- `test_cds_grammar_fail[/s.Mpc]`：DID NOT RAISE ValueError，属于 PASS_TO_PASS。
- 官方结构化报告：FAIL_TO_PASS 2 成功 / 1 失败；PASS_TO_PASS 730 成功 / 2 失败。
  报告还含截断的 `test_cds_grammar_fail[km` ID；保留原报告，不把截断 ID 擅自当作独立的新失败场景。

因此，Agent 测试全绿与官方失败可以在相同补丁上同时复现，不需要用评分故障或测试篡改解释。
baseline 原 pilot 也 unresolved，官方失败测试列表与 gated 相同；pilot 没有 baseline-only 成功实例。

## 状态与证据限制

旧 runner 没有导出 state.json / trace.jsonl，容器已自动删除。
不能声称拿到了旧 last_test_run / last_source_change 时间戳，也不能补造旧 trace。
stdout 可观察阶段 2、3、4、6 的成功推进；4→6 是“测试通过无需修复”的合法转换，
并不要求每次经过阶段 5。第 35 轮测试后第 36 轮进入交付，日志没有后续改码记录。
该信息不等于逐事件、逐时间戳的完整历史证明。

`delivery_clean()` 的现有实现要求阶段 >=6、最近测试 passed 且测试时间严格晚于最后变更；
等时、后续改码、后续红测的回归测试均通过。未发现支持 C 类漏洞的具体证据，
但旧状态缺失使历史时间戳不能逐项核验。

## 对正式批的影响

按本次已授权口径，A 类可以带局限继续：**门禁通过不代表官方评分通过，因为测试集不同，
门禁只能验证已提交测试证据，不能保证覆盖官方隐藏场景。** 不为此实例注入官方隐藏测试，
不修改抽样清单，不以重新跑出 resolved 为验收标准。

原 pilot 反向组合保留，不能作为“门禁有效”的正面证据。新 Astropy/SymPy gated 审计复核
使用修复后的独立输出目录，不并入正式批；新 state/trace 用来验证持久化和零拦截归类。
正式批仍须等待 CI 与新链路核验完成。

## 拦截计数影响与新链路

旧 Django gated 的 8 是重复计数：2 次工具拒绝被计两次，再加 4 次阶段推进拒绝；
日志可见独立拒绝为 6。Matplotlib 为 11→8，Sphinx 为 5→4，Astropy 为 5→4。
这些是对 stdout 的事后重算，不伪称来自旧 trace；历史原始字段不覆盖。
修复前 pilot 平均拦截次数不能直接与修复后正式批比较。

两个真实镜像的确定性容器验证已完成：Astropy、Django 各 3 个事件 / 2 个 blocked，
stats 与 trace 一致，最终 state 可被新审计器读取，模型请求为零。
`tests/fixtures/verified20_audit/` 明确区分原 pilot 的统计/官方报告摘要与新验证的 state/trace；
新文件不是历史重建。`tests/test_audit_pipeline.py` 覆盖真实产物解析、反向组合需显式审查、
计数损坏及交付时间戳无效时拒绝；连同 runner、采样和 delivery_clean 测试共 56 项通过。

## 独立模型复核完成（2026-11-10）

新 Astropy gated 补丁 SHA256 为
`af0e1e461558946c82f2bf539f50fba873e13fe619c776bf3ce696e621220539`，
44 轮、15,625 字符。它与原 pilot 补丁分开审查，不沿用旧补丁的豁免。

- `stats.json`、`state.json`、`trace.jsonl` 均可由新审计器读取；51 个事件，
  其中 3 次阶段拒绝，与 `gate_intercepts=3` 一致。
- 完整 trace 显示阶段按 1→2→3→4→6 推进；尝试进入交付前的三个不充分测试
  结果触发阶段拒绝，最终第 43 轮执行 `python -m pytest test_pb_repro.py -q`，
  `2 passed`，第 44 轮进入交付。
- 最近源码变更时间为 `1794295224.9678733`，最终测试时间为 `1794295392.5259397`，
  交付确认时间为 `1794295394.381405`。最终测试严格晚于源码变更，未见交付后改码。
- 新补丁仍只修改 CDS 实现、生成的解析表，并新增复现测试；未修改既有测试。
  新增两个测试函数覆盖连续除法和表格读取，没有断言混用除号与点乘应抛 ValueError。
- 官方仍 unresolved：FAIL_TO_PASS 2/3，PASS_TO_PASS 731/732，note=graded，
  `patch_successfully_applied=true`、`infra_failure=false`；失败 FAIL_TO_PASS 仍为
  `test_cds_grammar_fail[km/s.Mpc-1]`。

该新补丁也按上述具体证据归类 A：`gate_completed_insufficient`。完整新 trace 支持
此运行的交付新鲜度核验，不据此填补原 pilot 的历史缺失。复核不并入正式结果。
复核评分曾因宿主 runner 消失中断；已保留中断日志，对同一保存补丁使用独立 recovery
run ID 恢复官方评分，零新增模型请求，未以 resolved 为验收条件。

## 正式批首次运行与历史差异（2026-11-10）

正式批 `formal20_logicstar/results.csv` 中，该实例 baseline 与 gated 均为
resolved、note=graded；两份官方报告均显示 FAIL_TO_PASS 3/3、PASS_TO_PASS 732/732，
补丁成功应用且 `infra_failure=false`。

| 运行 | baseline | gated | gated 轮数 | gated 补丁字符 | gated 拦截 |
|------|----------|-------|-----------:|---------------:|-----------:|
| 原 pilot | unresolved | unresolved | 36 | 14,618 | 5（旧原始值；重算 4） |
| 独立审计复核 | 未运行 | unresolved | 44 | 15,625 | 3 |
| 正式批 | resolved | resolved | 37 | 13,887 | 4 |

正式 gated 的完整 trace 为 45 个事件、4 个 blocked，与 stats/state 一致，
交付新鲜度核验通过。正式 baseline 为 34 轮、346.1 秒，gated 为 37 轮、237.0 秒。
正式结果仅进入本批正式统计；旧 pilot 和复核的 A 类结论继续限定于各自补丁。
三次运行产生不同补丁和结果，保留运行间变异；没有控制实验，不将差异归因于门禁，
也不因新结果为 resolved 而覆盖旧失败记录。
