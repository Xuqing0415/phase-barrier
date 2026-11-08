# SWE-bench Verified 缓存 15 实例交叉验证（2026-11-08）

> 结论一句话：在本地可用的 Verified 缓存子样本上，baseline 为 12/15（80.0%），
> gated 为 13/15（86.7%）；没有 baseline 成功而 gated 失败的配对，门禁未降低解决率。
> 但 n=15、Wilson 区间重叠、McNemar 精确双侧 p=1.0，**不能宣称显著提升**。
> 本结果不是 Scale-50，也不是预注册的 12 仓库样本。

## 一、实验范围

- 数据源：`SWE-bench_Verified` test split。
- 样本：本地已缓存镜像的 17 个候选中选 15 个；覆盖 7 个仓库，不是随机样本，也不是预注册样本。
- 仓库：astropy 3、django 5、matplotlib 2、seaborn 2、flask 1、requests 1、scikit-learn 1。
- 难度：`<15 min fix` 7、`15 min - 1 hour` 7、`1-4 hours` 1、`>4 hours` 0。
  这是缓存可用性和配额共同作用的结果，不称为“难度均衡”。
- 两组：baseline（无门禁）与 gated（阶段门禁），同一实例配对。
- 预算：每组每实例最多 60 轮工具调用；并发 2。
- 评分：宿主侧官方 `swebench==5.0.2` harness，官方评测镜像，应用模型补丁后运行 `/eval.sh`。
- 选择偏差：只保留本地已有镜像的实例，可能偏向过去跑过 Lite/Verified 实验的仓库。
  预注册的 12 仓库样本在镜像补齐后作为第二批验证，**不与本批混合统计**。

选择命令与完整候选清单见
`benchmarks/swebench/manifests/verified15-cached-2026-11-07.md`；
选择元数据在 `benchmarks/swebench/results/verified15_cached_selection.json`。

## 二、结果

| 分片 | 实例数 | baseline | gated | 配对差异 |
|---|---:|---:|---:|---|
| Lite Scale-20（既有） | 20 | 18/20（90%） | 20/20（100%） | gated-only 2，baseline-only 0 |
| Verified 缓存子样本（本次） | 15 | 12/15（80%） | 13/15（86.7%） | gated-only 1，baseline-only 0 |

Wilson 95% 区间：

- baseline：80.0%，[54.8%, 93.0%]。
- gated：86.7%，[62.1%, 96.3%]。

配对结果：

- 两组都 resolved：12。
- baseline-only：0。
- gated-only：1（`astropy__astropy-7336`）。
- 两组都未 resolved：2（`django__django-10999`、`mwaskom__seaborn-3187`）。

唯一 discordant pair 为 gated 胜；McNemar 精确双侧检验 p=1.0。
因此本次数据支持“门禁没有降低解决率”，不支持“门禁提升了 6.7 个百分点”。

## 三、成本与门禁行为

| 指标 | baseline | gated |
|---|---:|---:|
| 总轮数 / 均值 | 674 / 44.9 | 656 / 43.7 |
| 总耗时 / 均值 | 4888.7s / 325.9s | 3357.7s / 223.8s |
| 门禁拦截 | 0 | 104（均值 6.9，最少 5，最多 10） |
| 走到阶段 6 | — | 14/15 |
| 交付全绿 | — | 14/15 |

墙钟耗时不应当作“门禁更快”的证据：本批 gated 平均耗时反而低于 baseline，
而既有 Lite-20 中 gated 约为 baseline 的 1.8 倍。两者方向相反，说明容器负载、
执行顺序、宿主休眠和缓存命中对耗时影响很大；耗时只能作为观察值，不是稳定结论。

门禁拦截次数没有出现 0 或 1 次这种异常低值，未发现“Agent 读了门禁源码后系统性规避”的迹象。

## 四、失败归因

### `astropy__astropy-7336`

baseline 官方评分为 unresolved，但根因不是实现错误：

- 模型补丁先成功应用到 `CHANGES.rst`、`astropy/units/decorators.py`
  和旧测试文件 `astropy/units/tests/py3_test_quantity_annotations.py`。
- 官方 test patch 需要把该旧测试文件重命名为
  `astropy/units/tests/test_quantity_annotations.py`；因为 baseline 已修改旧文件，
  重命名补丁无法应用，官方 report 记录 `patch_successfully_applied=false`。
- gated 组新增独立测试文件、没有触碰被重命名的文件，test patch 成功应用并 resolved。

因此这条 gated-only 差异在**官方评分口径**下成立，但不能单独作为“门禁提升了实现能力”
的证据；它同时反映了模型补丁与 test patch 的冲突风险。

### `django__django-10999`

两组都失败于同一批 2 个 FAIL_TO_PASS（F2P 0/2），PASS_TO_PASS 均为 10/10。
这是任务难度/补丁正确性问题，不是门禁截断。

### `mwaskom__seaborn-3187`

两组都失败于 1/2 个 F2P。baseline 在 60 轮上限被截断；gated 36 轮走到阶段 6 且
`gate_completed=1`，但补丁仍被判错。两组的失败方向一致，不能写成“门禁导致失败”。

### `django__django-10554`

gated 官方 resolved，但 60 轮结束时仍在阶段 4，`gate_completed=0`。
这说明“官方 harness resolved”与“门禁交付全绿”是两个独立指标：前者只表示补丁通过了
官方测试，后者要求阶段 6 完成、最近一次测试全绿且晚于最后一次源码/测试变更。
该实例不能作为“门禁流程有效”或“门禁交付全绿”的证据，只能作为“官方评分通过”计入
resolved 统计。

## 五、评分审计与 note 透传修复

30 次评分中无 `infra_failure`，无空补丁、超时或 driver error。完整逐实例审计见
`benchmarks/swebench/results/verified15_cached_audit.csv`。

本轮发现并修复了一个评分报告缺陷：`astropy-7336` baseline 的官方 report 是
`patch_successfully_applied=false`、`tests_status` 缺失，而旧版 `grade.py` 仍输出
`PB_GRADE_SUMMARY`，上层脚本因此把 CSV 的 `note` 标成 `graded`。

修复内容：

- `grade.py` 在 `patch_successfully_applied=false` 时读取该实例的 `test_output.txt`。
- 同时出现 `Checking patch` 与 `patch failed` / `patch does not apply` /
  `did not match any file(s) known to git` 时，标记为 `test_patch_conflict`。
- 其他未应用情况标记为 `patch_not_applied`；基础设施错误标记为
  `grade_infra_failure`；正常评分保持 `graded`。
- 通过 `PB_GRADE_NOTE` 透传到 `run_swebench_batch_container.py` 和
  `run_swebench_batch.py`，新产生的 CSV 不再无条件写 `graded`。

审计 CSV 中 `astropy__astropy-7336` baseline 的 `eval_note` 已标为
`test_patch_conflict`。原始结果 CSV 保留当时产物，不重写 `note`；本次 resolved
统计也不受该标注修复影响。该修复只影响后续运行。

## 六、隔离局限

- Agent 容器没有 chroot/bwrap；Agent 可以通过 shell 读到 `/pb` 下的部分门禁源码和
  `/testbed/.git`。
- 提供给 Agent 的 `/pb/dataset_agent.json` 已脱敏，只含
  `instance_id`、`repo`、`base_commit`、`problem_statement`；
  `patch`、`test_patch`、`FAIL_TO_PASS`、`PASS_TO_PASS`、`hints_text` 均未挂载。
- `read_file` 的越界路径已被 `/testbed` 限制并返回 `PermissionError`；
  但 shell 仍可读取 `/pb/anti_shortcut` 源码。这是已知隔离局限。
- 本次实验回答的是“门禁对正常 Agent 解决率的影响”，不是“对抗性 Agent 能否绕过门禁”。
  若要回答后者，需要 chroot/bwrap 或独立安全评测环境。
- 日志显示 Agent 确实发现了 `/pb` 挂载；这本身是观察结果，不应隐藏。

## 七、异常补丁观察

`scikit-learn__scikit-learn-10297` 的 gated 补丁为 117,007 字符，明显大于 baseline
（2,383 字符）。差异主要来自 Agent 新增的 2,908 行 `typing_extensions.py` 兼容层和
89 行 `test_pb_repro.py` 复现测试；核心 `ridge.py` 改动只有 13 行新增、7 行删除。
官方 harness 判定 resolved。初步检查未发现测试篡改或绕过证据，但该补丁属于
异常大补丁，不应作为“干净实现”的样本引用，建议人工复核兼容层来源。

补丁大小分布（字符）：

| 组别 | 最小 | 中位 | 均值 | P90 | 最大 |
|---|---:|---:|---:|---:|---:|
| baseline | 1,118 | 2,292 | 2,474.5 | 3,649 | 6,540 |
| gated | 1,458 | 3,981 | 11,874.1 | 10,840 | 117,007 |

`scikit-learn-10297` 是明显离群值：其 gated 补丁为同实例 baseline 的 49.1 倍、
gated 中位数的 29.4 倍、gated P90 的 10.8 倍。gated 第二大补丁是
`django-10973` 的 10,840 字符，因此这不是“一批实例普遍堆代码”的分布，而是单个
实例的异常行为。是否由门禁诱导仍需结合 trace 人工分析；在确认前，不应把它当成
门禁提升或门禁负面效应的证据。

## 八、复现

选择命令（来自 manifest）：

```bash
python scripts/select_swe_tasks.py \
  --dataset .pytest_tmp/verified15/cached17_dataset.json \
  --count 15 --balance-difficulty --seed 7 \
  --out .pytest_tmp/verified15/cached15.json
```

运行命令（参数与本次一致；路径按本机调整）：

```bash
python scripts/run_swebench_batch_container.py \
  --tasks .pytest_tmp/verified15/cached15.json \
  --dataset .pytest_tmp/verified15/cached17_dataset.json \
  --grade-python .pytest_tmp/sweb_venv/Scripts/python.exe \
  --results .pytest_tmp/verified15/cached15_results.csv \
  --agent-runs .pytest_tmp/verified15/cached15_agent_runs \
  --eval-runs .pytest_tmp/verified15/cached15_eval_runs \
  --log-dir .pytest_tmp/verified15/cached15_logs \
  --modes baseline,gated --max-turns 60 --concurrency 2 --skip-existing
```

复算：

```bash
python benchmarks/swebench/analyze_results.py \
  --results benchmarks/swebench/results/verified15_cached.csv
```

## 九、限制与下一步

- n=15，缓存样本，7 个仓库；Wilson 区间重叠，不能做排行榜或显著性声明。
- 不是 Scale-50，不能与 Scale-20 合并成“50 个配对”，也不能替代 Scale-50。
- 预注册的 12 仓库 Verified 样本仍在 `.pytest_tmp/verified15/verified_15_offset.json`；
  等镜像补齐后单独跑第二批，两批结果方向一致才有更强的交叉验证价值。
- 评分 note 的 `test_patch_conflict` 透传已修复；第二批应使用修复后的 `grade.py`，
  避免同类审计盲点。
- 人工复核 `scikit-learn-10297` 的 117k 补丁，再决定是否在对外材料中引用该实例。

## 十、结论

在两个独立分片上，门禁都没有出现“baseline 成功、gated 失败”的配对：
Lite-20 为 18/20 -> 20/20，Verified 缓存-15 为 12/15 -> 13/15。
这排除了“门禁必然降低解决率”的简单解释，但 n=15 的 Verified 子样本仍不足以证明
显著提升；目前最稳妥的结论是**方向一致、无回归、需扩样**。
