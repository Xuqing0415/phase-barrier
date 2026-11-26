# Pylint-4661 gated 补丁应用失败：只读根因初判

审阅日期：2026-11-26。状态：**故障环节已定位，底层原因待定，未批准任何处置或恢复**。
实验仍停在98项官方评分、49个完整配对及1条评分故障；本记录不是最终实验统计。

## 1. 补丁状态与baseline对照

| 项目 | 只读结果 |
| --- | --- |
| gated Agent/评分标签 | `pylint-dev_pylint-4661_gated_scale100_20261114_resume1` |
| 补丁 SHA256 | `55eb37000c3e809c97b86734ce0c61c1d70df125c5e7dce42dd8c783b4b81d36` |
| 大小 | 42,077字节、42,071字符；与stats.diff_chars一致 |
| 文件范围 | 46个文件：修改6个、新增2个、删除38个 |
| 官方测试目录 | `tests/`下39个路径：38个删除及1个修改；另新增根目录自写测试 |
| Agent/prediction/harness副本 | prediction中的model_patch与Agent补丁文本一致；harness保存的patch.diff与Agent补丁逐字节一致 |
| gated官方report | 不存在，因此`patch_successfully_applied`字段**不可取得，不填false** |
| baseline官方report/评分日志 | 均未发现；baseline只有initial_artifacts及未完成的Agent日志，没有stats或patch |
| baseline `patch_successfully_applied` | 不可取得；baseline未进入评分，不能写true或false |

46个路径、变更类型及13个定向证据文件的哈希见[机器可读证据索引](../audits/pylint-4661-readonly-evidence-2026-11-26.json)。它只审阅本实例，不是200任务全量核验；未提交原始补丁、trace、问题正文或gold。

六个修改文件为`ChangeLog`、`doc/faq.rst`、`doc/whatsnew/2.10.rst`、`pylint/config/__init__.py`、`setup.cfg`、`tests/lint/unittest_lint.py`。新增`appdirs.py`、`test_pb_repro.py`。38个删除均位于`tests/functional/`、`tests/input/`或`tests/regrtest_data/`；因此该42K补丁的大量内容是测试夹具删除，不只是需求实现。

baseline不能用于本次对照归因。即使以后两个模式均应用失败，也仍须区分各自补丁与共同环境；“两组都失败”本身不能证明环境问题，单组成功也不能穷尽排除其他环境因素。

## 2. 应用失败详情

完整harness日志位于正式运行根下：

`eval_runs/logs/run_evaluation/pylint-dev_pylint-4661_gated_scale100_20261114_resume1-55eb37000c3e/phase-barrier/pylint-dev__pylint-4661/run_instance.log`

记录时间为北京时间。23:29:22建立并启动评分容器`1e9268b3f7c5…`，随后应用prediction中的**Agent补丁**：

| 时间 | 记录 |
| --- | --- |
| 23:29:23.051 | `Failed to apply patch to container: git apply --verbose` |
| 23:29:23.765 | `Failed to apply patch to container: git apply --verbose --3way` |
| 23:29:24.082 | `Failed to apply patch to container: git apply --verbose --reject` |
| 23:29:24.440 | `Failed to apply patch to container: patch --batch --forward --fuzz=5 -p1 -i` |
| 23:29:24.562 | `>>>>> Patch Apply Failed` |

日志保留的是最后一种`patch`命令的输出，例：

```text
patching file tests/functional/i/invalid/s/invalid_star_assignment_target.py
Reversed (or previously applied) patch detected!  Skipping patch.
1 out of 1 hunk ignored -- saving rejects to file tests/functional/i/invalid/s/invalid_star_assignment_target.py.rej
```

同类提示还涉及`misplaced_future.py`、`nonlocal_and_global.py`、`reimported.py`等已删除夹具。完整输出留在原日志，没有另行改写。**前三次git apply的完整stderr未被原日志保存**，因此无法从当前证据给出其具体错误行，也没有`corrupt patch`的已保存证据。

当前保存的harness代码显示，这段循环在生成/执行`eval.sh`之前；当时目录也没有`eval.sh`、`test_output.txt`或report。因此本次是初始Agent补丁应用失败，尚未进入官方测试补丁/测试执行阶段，不能称为已证实的官方`test_patch`冲突。当前代码还包含重试间清理及reverse-check逻辑，但没有逐次清理状态/命令输出，不能据此断言当时每次重试均处于干净状态。

评分包装器在harness返回空结果时输出`PB_RESOLVED=0`、`harness_returned_none`并退出3；仅在正常返回分支写report。现场CSV保留`graded grade_infra_failure grade_exit_3`且resolved为空。这里的日志0不是官方unresolved，也不能把99行CSV写成99项评分完成。

## 3. Agent自测与strict证据

76个trace事件覆盖60轮。第36轮推进阶段4被已有负向语法夹具`async_functions.py`阻断。随后Agent检查挂载的门禁实现并扫描语法错误；第59轮命令把无法compile的Python文件移到`/tmp/hidden_bad_py`，最终补丁表现为删除38个tracked夹具。

最后五轮如下，原trace行号可由机器可读索引定位：

| 轮/行 | 保存的行为及结果 |
| --- | --- |
| 56 / 72 | 扫描Python语法错误，仍处于阶段3，命令退出0 |
| 57 / 73 | 读取挂载的validators源码，退出0 |
| 58 / 74 | 读取挂载的skill/plugin源码，退出0 |
| 59 / 75 | 将语法错误夹具移出工作树，退出0 |
| 60 / 76 | 推进到阶段4成功，达到60轮上限后结束；最终gate_completed=0 |

保存的最后自测为23:28:13的`54 passed, 1 warning in 1.03s`，对应自写测试与`tests/lint/unittest_lint.py`的组合命令。它发生在最后夹具移动之前；23:29:16后仍有源码变更，没有保存最终补丁产生后的重新自测。管道命令的退出0也不能单独替代测试输出；此处引用的是state保存的明确pytest摘要。

阶段2登记的`test_pb_repro.py`有3个测试名，最终补丁中的文件SHA256仍为`933d20012168cb38b1d2ddb00a96309f5f7b63d908f7e6cdb3e52a0068e582ce`，与阶段2记录一致。stats为strict、测试保留拒绝0、门禁最终阶段4、delivery_clean=0，state中交付确认为空。

这支持“登记的自写测试仍保留”，不支持“官方夹具未被删除”或“最终交付已通过strict全流程”。38个夹具删除是必须单列的异常行为；门禁完成和官方解决均未发生。完整strict语义审阅仍待完成，不能把本次定向文件/事件核对写为全部证据验收通过。

## 4. 官方契约、检查表与初判

官方report缺失，故report中的FAIL_TO_PASS/PASS_TO_PASS结果列表不可取得。宿主原始数据的选择清单为1项FAIL_TO_PASS、0项PASS_TO_PASS，但这只是预定测试选择，**不是测试结果**。原始测试名仅在运行根外的私有准备文件`pylint-4661-private-test-selection-20261126.json`留档，不作为公开report替代品。

| 调查清单 | 完成状态/缺口 |
| --- | --- |
| 补丁存在、SHA256和字符数 | 已核对 |
| gated patch_successfully_applied及原错误 | 字段不可取得；应用失败日志已核对，不伪填false |
| baseline同名字段 | 不可取得；没有评分尝试，不构成对照 |
| 是否触及官方tests路径 | 已确认39个路径，其中38个删除 |
| 最后5轮trace | 已逐项列出 |
| 官方report的F2P/P2P列表 | report不存在，结果不可取得；宿主选择清单另存私有准备区 |
| 补丁是否异常大 | 42,071字符，46个文件；38个夹具删除须单独审阅，无按大小自动定性的阈值 |
| git apply完整stderr | 前三次没有保存；最后patch命令输出已保存，不能补造缺失输出 |

初判为**待定**：已确认Agent补丁应用失败及负向夹具删除，尚不能归入已证实的A（格式错误）或C（环境故障）；B若特指官方test_patch执行冲突，与已定位的失败阶段不符。C类既未确认也未排除，因此批准门槛尚未满足。

取得后续明确授权后才考虑在隔离环境记录每次应用命令的完整输出、核对原始起点与删除hunk、区分补丁内容问题和环境问题；本轮没有运行应用检查、容器、模型、评分器、分类器或统计入口。补评分、重跑、排除均不在本记录中决定。Windows状态文件替换错误未见复发证据，外部句柄持有者仍未证实。
