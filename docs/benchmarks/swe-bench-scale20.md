# SWE-bench Lite Scale-20 实测报告（phase-barrier 门禁 vs 无门禁）

> 结论一句话：在官方 SWE-bench 评测容器内，同模型同预算下，加门禁的 gated 组
> **20/20 resolved（100%）**，无门禁的 baseline 组 18/20（90%）；门禁累计拦截 78 次，
> 把 2 个 baseline 失败的实例救了回来，且没有拖累任何一个成功实例。代价是平均墙钟耗时
> 约 1.8 倍。**n=20，只用于同条件相对对比，不构成排行榜结论。**

原始数据与复算脚本都在仓库里，本文每个数字都能一条命令重算，不需要重跑 Agent。

## 一、实验设置

| 维度 | 取值 |
|---|---|
| 数据集 | `SWE-bench/SWE-bench_Lite`，`test` 分片（canonical，含 `eval_script`） |
| 实例数 | 20（分层抽样 + must-repo 配额：django 4 / sympy 4 / astropy 2 / matplotlib 2，其余 8 个覆盖 seaborn、flask、requests、xarray、pylint、pytest、scikit-learn、sphinx） |
| 组别 | `baseline`（无门禁）/ `gated`（`AntiShortcutSkill` 阶段门禁），同实例配对 |
| 模型 | `deepseek-v4-flash`（同一 API、同一温度与提示词） |
| 预算 | 每组每实例最多 60 轮工具调用 |
| 运行环境 | 官方镜像 `swebench/sweb.eval.x86_64.*`，**Agent 在容器内运行**；评分在宿主用官方 `swebench==5.0.2` harness 跑 `/eval.sh` |
| 并发 | 3 |
| 运行日期 | Agent 2026-09-10；gated 组在 §4 的修复后于 2026-09-12 重跑 |

复现命令（需要 Docker、已缓存对应官方镜像、宿主评分 venv、`DEEPSEEK_API_KEY`）：

```bash
python scripts/select_swe_tasks.py --dataset .pytest_tmp/bench_data/lite/dataset.json \
  --count 20 --must-repo django/django sympy/sympy --per-repo 2 --seed 20260910 \
  --out .pytest_tmp/bench_data/lite/tasks_scale20.json

python scripts/run_swebench_batch_container.py \
  --tasks .pytest_tmp/bench_data/lite/tasks_scale20.json \
  --dataset .pytest_tmp/bench_data/lite/dataset.json \
  --grade-python .pytest_tmp/sweb_venv/Scripts/python.exe \
  --results benchmarks/swebench/results/scale20_lite_container.csv \
  --log-dir .pytest_tmp/bench_data/container_logs \
  --modes baseline,gated --max-turns 60 --concurrency 3
```

## 二、结果

数据文件：`benchmarks/swebench/results/scale20_lite_container.csv`（40 行，`note` 全为 `graded`）。

| 组别 | n | resolved | resolve 率 | Wilson 95% 区间 | 门禁拦截 | 走到阶段 6 | 交付全绿 | 空补丁 | 平均轮数 | 平均/中位/P90 耗时 |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 20 | 18 | 90% | [69.9%, 97.2%] | 0 | — | — | 0 | 43.1 | 262s / 256s / 576s |
| gated | 20 | 20 | 100% | [83.9%, 100.0%] | 78 | 14 | 14 | 0 | 49.8 | 462s / 349s / 927s |

「交付全绿」= `State.delivery_clean()`：阶段 6 + 最近一次测试全绿 + 晚于最后一次源码变更。

一行复算：

```bash
python benchmarks/swebench/analyze_results.py --results benchmarks/swebench/results/scale20_lite_container.csv
```

```text
baseline n= 20 resolved= 18 (90%) wilson95=[69.9%,97.2%] intercepts=0   stage6=0  clean=0  empty=0 turns=43.1 sec=262/256/576
gated    n= 20 resolved= 20 (100%) wilson95=[83.9%,100.0%] intercepts=78 stage6=14 clean=14 empty=0 turns=49.8 sec=462/349/927
rescued=['matplotlib__matplotlib-22711', 'sympy__sympy-11400'] regressed=[]
```

## 三、关键发现

1. **门禁没有降低解决率，反而把 2 个失败实例救了回来，且 0 例拖累。**
   配对比较：`matplotlib__matplotlib-22711`、`sympy__sympy-11400` 在 baseline 未通过，
   在 gated 通过（两者都走到阶段 6 且交付全绿）；没有任何实例出现反向翻转。
2. **78 次拦截全部被 Agent 合规修复。** gated 组平均每实例 3.9 次拦截，
   分布为 0/2/4/5/6/7 次；拦截原因集中在「未写测试就想写实现」「没跑测试就想交付」。
3. **代价是耗时：平均 262s -> 462s（约 1.8x），中位 256s -> 349s（约 1.4x）。**
   P90 从 576s 涨到 927s。轮数只从 43.1 涨到 49.8（+16%），说明增加的主要是
   「按 SOP 补证据 + 重跑测试」的墙钟时间，不是 API 轮次。
4. **统计显著性：n=20 不足以下强结论。** 两组 Wilson 区间有重叠（90% 的下界 69.9%
   低于 100% 的上界），因此不能声称「门禁显著提升解决率」；但「不降低、且方向为正」
   在 20 个配对实例上是一致的。这也是继续做 Scale-50 / Verified 复核的动机。
5. **6 个 gated 实例在阶段 3/4 结束（60 轮预算耗尽），但补丁仍通过官方评测。**
   它们没有触发 `delivery_clean`：门禁没有放行交付，但评测只看补丁。
   这说明「门禁拦住流程」与「官方评测判定 resolved」是两件事，报告中不应混为一谈。

## 四、根因分析：gated 从 65% 到 100%

首轮 gated 只有 **13/20（65%）**，且失败形态高度一致：**6 个实例永久卡在阶段 2**，
Agent 反复重试也无法推进（本地原始数据 `.pytest_tmp/bench_data/results_scale20/results.csv`）。
两个根因都在门禁侧：

1. **语言误判**：语言探测按「标志文件先到先得」，django / sphinx 这类仓库根目录有
   `package.json`（前端或文档工具链），于是 Python 仓库被判成 `javascript`，
   用 JS 启发式去校验 `.py` 测试 → 报「断言关键字不足（0 < 1）」。
   修复：多标志命中时按**源文件数**消歧（跳过 `node_modules`/`.venv`/`build` 等），
   无法消歧再回退原优先级（`anti_shortcut/languages/__init__.py`）。
2. **存量测试白嫖**：`validate_tests` 扫描整个存量仓库（几十年历史夹具），
   Agent 不写新测试也能「通过」。修复：新增 `stage2_test_scope: changed`（默认），
   只校验相对 HEAD 新增/修改的测试文件；非 Git 仓库或工作区干净时自动回退
   （`anti_shortcut/validators.py`、`anti_shortcut/paths.py`、`anti_shortcut/config.py`）。

修复后同一批量重跑：6 个卡死实例 **resolved 0 -> 1**，gated 组 13/20 -> 20/20。
回归测试 `tests/test_stage2_scope.py`（13 项）用 django / sphinx 的真实目录布局复现，
并覆盖「拿存量测试蒙混」的绕过用例。

## 五、harness 陷阱（实测踩到，已修复）

这两个坑会让**整批数据失真**，做任何 Agent 评测都值得对照检查：

1. **陈旧补丁被当作本轮结果评分。** Agent 容器内 import 失败秒退时，上一轮的
   `model_patch.diff` 还在挂载目录里，驱动会拿它当本次结果——本轮 20 行里一度有 14 行
   是上一轮的假数据。修复：运行前清空同名 label 目录，并用 `patch_is_fresh()`
   校验补丁 mtime 晚于本轮开始时间（`scripts/run_swebench_batch_container.py`）。
2. **并行写坏共享依赖卷。** Docker Desktop 卷挂载下 `mkdir` 锁不互斥，
   并发 `pip install --target` 会把 `pb_gate_deps` 写坏（实测缺 `typing_extensions`，
   Agent 直接 import 失败）。修复：bootstrap 改为「`rm -rf` -> 安装 ->
   `import pydantic, yaml, structlog` 校验 -> 才落 `.ready`」。

另外两个已修的坑：Windows 写出的 `eval.sh` 若带 CRLF 会让 `set -e` / `conda activate`
静默失败（`grade.py` 强制 LF 写入）；官方 harness 的 `report.json` 按
`run_id/model/instance` 缓存，复用 run_id 会直接返回旧结论（现按任务 + 提示词 sha256 前 12 位取 run_id）。

## 六、Verified 分片复核

把已产出的补丁换到 `SWE-bench/SWE-bench_Verified` 分片重新评分（不重跑 Agent），
4 个实例 × 2 组 = 8 份判定：**8/8 与 Lite 一致，无翻转**。

数据：`benchmarks/swebench/results/verified_regrade.csv`。

| 实例 | baseline Lite -> Verified | gated Lite -> Verified | Verified F2P / P2P |
|---|---|---|---|
| `astropy__astropy-12907` | 1 -> 1 | 1 -> 1 | 2 / 13 |
| `astropy__astropy-14182` | 1 -> 1 | 1 -> 1 | 1 / 9 |
| `django__django-10914` | 1 -> 1 | 1 -> 1 | 1 / 98 |
| `scikit-learn__scikit-learn-10297` | 1 -> 1 | 1 -> 1 | 1 / 28 |

```bash
python benchmarks/swebench/regrade_cross_shard.py \
  --results benchmarks/swebench/results/scale20_lite_container.csv \
  --target-dataset .pytest_tmp/bench_data/verified/dataset.json \
  --agent-runs .pytest_tmp/bench_data/agent_runs \
  --grade-python .pytest_tmp/sweb_venv/Scripts/python.exe \
  --out-dir .pytest_tmp/bench_data/eval_runs_verified \
  --out-csv benchmarks/swebench/results/verified_regrade.csv
```

## 七、已知限制

- **n=20，非排行榜结论**；Wilson 区间有重叠，方向性结论不等于统计显著。
- 单一模型、单一框架（DeepSeek 工具循环），未验证跨模型可迁移性。
- gated 组 `seconds` 是容器内 Agent 墙钟耗时；宿主休眠会同时拉长它与看门狗，
  这类行已按 `container_timeout` 剔除后重跑，入库行 `note` 全为 `graded`。
- 门禁的价值体现在「流程合规 + 交付前测试全绿」，官方评测只考察补丁正确性；
  两者不等价（见 §三.5）。

## 八、下一步

- 扩到 Scale-50（Verified 采样 30 + 现有 20），补统计检验与被门禁救回实例的逐例分析。
- 用 `benchmarks/swebench/analyze_results.py` 统一出表，避免手抄数字。
- 把本报告的 baseline/gated 对照纳入对外材料（README、博客、知乎 / Dev.to / HN）。
