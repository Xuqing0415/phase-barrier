# Seaborn-3069：缓存批 pilot 与正式批对照

2026-11-13，对照原补丁、原官方报告及现存轨迹，不重跑 Agent。
这里的 pilot 是 `.pytest_tmp/verified15/pilot5_results.csv`，不是正式批启动前的链路 pilot-5；
后者包含 Astropy-14369、Django-16901、Matplotlib-23476、Sphinx-9281、SymPy-18211，不包含 Seaborn。

| 维度（gated） | 缓存批 pilot | 正式批 |
|---|---|---|
| 轮数 | 60 | 60 |
| 最终阶段 / 门禁完成 | 3 / 0 | 4 / 0 |
| 补丁字符数 | 4,312 | 4,805 |
| 补丁 SHA256 前缀 | `3565e279e47e` | `a083803d4a68` |
| 官方结果 | unresolved | resolved |
| 官方 FAIL_TO_PASS | 1/2 | 2/2 |
| 官方 PASS_TO_PASS | 94/94 | 94/94 |
| 修改文件 | `seaborn/_core/plot.py`，新增自写测试 | 同一实现文件，新增自写测试及文档修改 |

## 已定位的补丁行为差异

pilot 唯一失败的 F2P 是 `tests/_core/test_plot.py::TestScaling::test_nominal_y_axis_tweaks`。
原官方日志 1907–1915 行记录：显式设置 `lim=(-1, 2.1)`，期望 y 轴范围保持该值，
实际得到 `(2.1, -1.0)`。pilot 补丁对 Nominal y 轴在存在显式 limits 时仍执行反转，
直接对应这次断言失败；pilot 的 8 个自写用例未覆盖“显式 y 数值 limits 保持原顺序”。

正式补丁只在没有显式 limits 时设置分类轴的默认范围，并保留用户指定的 limits；
正式自写 `test_nominal_y_axis_tweaks` 明确断言 `.limit(y=(-1, 2.1))` 的结果仍为 `(-1, 2.1)`。
因此两次不同的官方结果已有具体测试/补丁解释，不能只写“截断导致 unresolved”。
此处是原补丁和原失败日志的静态对照，未新增 Seaborn 容器重放，不声称做过控制实验。

## 阶段与轮次差异

pilot 没有保存 state/trace；stdout 能确认第 43 轮进入阶段 2、第 45 轮进入阶段 3，
第 50 轮显示自写测试通过，第 60 轮结束仍为阶段 3。日志截断，不能补造完整历史。
正式 trace 在第 35、44、48 轮分别成功进入阶段 2、3、4。
第 55、57、59 轮申请继续推进均因“未检测到测试运行记录”被拒绝；
第 58 轮的 shell 命令包含 `cd /testbed && pytest ... | tail ...`，
该命令有实际输出但没有得到门禁认可的测试运行记录，最后停在阶段 4。

两次均用完 60 轮且门禁未完成，但实现行为、测试覆盖和推进轨迹不同。
已定位的显式 y 范围处理差异解释原官方断言结果，不足以确定为什么 Agent 选择了不同补丁，
也不能隔离模型采样、代码修复、环境和轮数预算各自的影响。
本次不修改测试命令识别或门禁，也不将门禁未完成等同于官方评分受阻。

## 证据与统计口径

pilot 证据位于 `.pytest_tmp/verified15/pilot5_agent_runs/mwaskom_seaborn-3069_gated/`，
官方日志位于 `pilot5_eval_runs/logs/run_evaluation/mwaskom_seaborn-3069_gated-3565e279e47e/phase-barrier/mwaskom__seaborn-3069/`；
截断 stdout 位于 `pilot5_logs/mwaskom_seaborn-3069_gated.container.log`。
正式证据位于 `.pytest_tmp/verified20/formal20_logicstar/agent_runs/mwaskom_seaborn-3069_gated_verified20_formal/`
及对应 `eval_runs`。输入文件与哈希保存于 `formal_parallel_audit_evidence.json`。
缓存-15 和正式批双组均 resolved 的一致性保留，pilot 不进入正式统计；本批 Seaborn 属复用样本单列。
