# 教程：真实 SWE-bench 评测（v0.41.0 harness）

> 上一教程 [SWE-bench 门禁基准](swe-bench-gate.md) 的 `benchmarks/swe_bench_gate.py`
> 是**任务模拟器**：内置合成任务，验证「带门禁的 Agent 过程合规」。本教程的
> `benchmarks/swebench_runner.py`（v0.41.0）是**真实数据集评测的编排层**：加载与官方
> SWE-bench 同构的实例清单，双组（基线 / 门禁）跑 Agent 并聚合 resolve / 拦截指标。
> 真实隐藏测试打分仍依赖官方 swebench harness / Docker 环境（见下文第 3 步）。

## 一、快速冒烟（无任何外部依赖）

```bash
python benchmarks/swebench_runner.py --synthetic 20 --fail-fast
```

合成模式用固定种子确定性模拟 baseline / gated 两组结果（gated resolve 率 ≥ baseline、
存在拦截），用于验证管线与阈值逻辑；CI bench job 每次 PR 也会跑一次。

```bash
python benchmarks/swebench_runner.py --synthetic 20 --json
```

## 二、实例清单与回报标记

实例清单是与官方 SWE-bench 数据集同构的 JSON：

```json
[
  {
    "instance_id": "django__django-11039",
    "repo": "django/django",
    "base_commit": "8d9f5b2...",
    "problem_statement": "# 问题描述 ...",
    "patch": "...",       // 黄金补丁（供对照，harness 透传）
    "test_patch": "..."
  }
]
```

支持 `[{...}]` 或 `{"instances": [...]}` / `{"data": [...]}` 包装。

Agent / 隐藏测试执行器按以下 stdout 标记回报结果（harness 只做编排与统计）：

| 标记 | 含义 |
|------|------|
| `PB_RESOLVED=1\|0` | 该实例最终是否 resolve（由官方 harness 跑隐藏测试后写入） |
| `PB_GATE_INTERCEPTS=N` | 门禁拦截次数（基线无门禁恒为 0） |

未写任何标记时按退出码推断：0 视为 resolve。

## 三、真实评测步骤

真实评测需要用户自备环境（官方 swebench 依赖 Docker 与数据集镜像，资源密集）：

1. **准备 SWE-bench 环境**：`pip install swebench`，拉取数据集
   （如 `princeton-nlp/SWE-bench_Verified`）与对应 repo 镜像（`swebench/sweb.eval.x86_64.*`）。
2. **准备实例清单**：从数据集提取 `instance_id` / `repo` / `base_commit` /
   `problem_statement` / `test_patch`，写入 `instances.json`。
3. **包装 Agent 命令**：写一个 `run_agent.sh`，对给定实例：checkout `base_commit`
   → 启动 Agent（基线 / 经 phase-barrier 门禁）→ 跑官方 harness 的隐藏测试
   → 按上表打印 `PB_RESOLVED` / `PB_GATE_INTERCEPTS`。命令模板占位符：
   `{id}`（实例 id）与 `{workdir}`（harness 预创建的实例工作目录）。
4. **运行双组评测**：

```bash
python benchmarks/swebench_runner.py \
  --instances instances.json \
  --cmd-baseline 'bash run_agent.sh {id} {workdir} no-gate' \
  --cmd-gated     'bash run_agent.sh {id} {workdir} with-gate' \
  --timeout 3600 --json --output report.json
```

5. **阈值门禁**（`--fail-fast`）：

```bash
python benchmarks/swebench_runner.py \
  --instances instances.json --cmd-baseline ... --cmd-gated ... \
  --fail-fast
```

默认判定：gated resolve 率 ≥ baseline，且 gated 拦截数 > 0（证明门禁实际生效）；
可用 `--no-intercepts-check` 关闭后者。失败退出码 1，便于纳入发布门禁。

## 四、报告解读

报告含 baseline / gated 各自的 `resolve_rate`、`gate_intercepts`、
`gate_intercept_rate`、`avg_duration_s`，以及逐实例 `rows`。典型结论形态：

- 若 gated resolve 率与 baseline 相当且拦截率 > 0：门禁拦截了跳步但对最终解决率无
  显著负作用；
- 若 gated 显著更高：跳步导致的“假绿”被消除，质量受益；
- 若 gated 显著更低：门禁约束过严或证据要求与任务节奏不匹配，需放宽阶段配置。

## 五、局限与后续

- harness 不内置官方 swebench 容器与隐藏测试打分；真正的 resolve 判定由调用方
  包装脚本完成（推荐复用官方 harness 的 `run_evaluation`）。
- 建议从小样本（20–50 实例）开始，控制 Docker 与算力成本；补丁通过率等细粒度指标
  可由包装脚本继续扩展标记（本 harness 已对额外 `PB_*` 行保持透传兼容）。


## 六、实测记录（2026-09-06：Windows 本机跑通官方 harness 并产出首批数据）

数据：官方 `SWE-bench/SWE-bench_Lite` dev 分片（避开公共 test），3 个实例、跨两个仓库：
`marshmallow-code__marshmallow-1343` / `-1359` 与 `pydicom__pydicom-1256`。
评分用官方 `run_evaluation`（swebench 5.0.2）+ 官方 Docker 评测镜像，gold patch 对照
resolved=1（链路自检通过）。Agent 为 DeepSeek `deepseek-v4-flash`，
同实例同环境跑 baseline / gated 双组。

| 实例 | 组别 | resolve | 门禁终态 | 说明 |
|---|---|---|---|---|
| marshmallow-1343 | baseline | 1 | - | 33 轮 / 152s |
| marshmallow-1343 | gated | 1 | 阶段 6 | 37 轮，推进被拒 1 次（证据不足），写/执行拦截 0 |
| marshmallow-1359 | baseline | 1 | - | 32 轮 / 156s；补丁与 gold 逐行等价 |
| marshmallow-1359 | gated | 1 | 阶段 6 | 33 轮，推进被拒 1 次，写/执行拦截 0 |
| pydicom-1256 | baseline | 1 | - | 33 轮 / 136s；`jsonrep.py` 向 `from_json` 补传 bulk handler |
| pydicom-1256 | gated | 1 | 阶段 6 | 27 轮 / 87s；推进/写/执行拦截均 0，证据链完整 |

要点：
- 门禁组均完整走通 spec → 复现测试（修复前红）→ 实现 → 测试通过 → 交付；先测后码顺序真实成立。
- 存量仓库需配置 `require_assert_per_test: false`（仓库历史测试无断言会误伤阶段 2）与
  `min_test_functions: 1`，属产品既有配置项。
- pydicom-1256 gated 组 0 拦截即通关：模型在门禁提示下自觉按 spec → 复现测试 → 实现 →
  测试通过 → 交付的顺序行动，未尝试越权；新增复现测试 `tests/test_pb_repro.py` 为带
  真实断言的先红后绿用例（SQ 内 BulkDataURI 交给 handler），与 baseline 走同一修复点，
  官方 F2P 均通过。
- 数据质量备注：`pydicom-1413` gold 在官方镜像上自身 unresolved（patch 可应用、F2P
  3/3 通过，但 PASS_TO_PASS 有 2 例回归），属实例镜像环境不匹配，已排除出 Agent 评测。
- Windows 侧工程问题与修复：swebench 5.x 需用带 `image` 列的 `SWE-bench/SWE-bench_Lite`；
  数据集与 harness 写文件需 CRLF→LF（容器内 eval.sh 带 CR 行尾会全挂）；Docker Hub 不通时经镜像站拉取后 `docker tag` 回官方名；`PYTHONUTF8=1`。
- 样本仍小（3 实例 × 2 组，跨 marshmallow / pydicom 两生态）且实例偏易：本组实验门禁对
  resolve 无负作用（3/3 双组全过）且留下完整合规证据链；量化影响需扩至 10-30 实例
  （django / sympy 等更难点）。完整记录见
  `.pytest_tmp/bench_data/report.md`（本地实验产物，未入库）。

## 七、批量扩展：选任务 + 双组编排脚本（2026-09）

单实例手工跑通后，仓库新增两个脚本把“3 实例 → 10-30 实例”的启动门槛降到一条命令：

### 7.1 选任务：`scripts/select_swe_tasks.py`

按仓库分层抽样，可指定必须覆盖的仓库、排除已知数据质量问题实例（如 `pydicom__pydicom-1413`），
固定随机种子保证可复现：

```bash
# dev 分片（23 实例，6 仓库）抽 12 个
python scripts/select_swe_tasks.py --dataset swebench_dev.json --count 12 \
    --exclude pydicom__pydicom-1413 --out tasks_dev12.json

# 含 django/sympy 的 Lite test 抽 20 个，django 与 sympy 各至少 2 个
python scripts/select_swe_tasks.py --dataset swebench_test.json --count 20 \
    --must-repo django/django sympy/sympy --per-repo 2 --out tasks_lite20.json
```

注意：dev 分片只有 marshmallow / pvlib / pydicom / astroid / pyvista / sqlfluff，**没有
django / sympy**；要覆盖高难度仓库需用含 `image` 列的 `SWE-bench/SWE-bench_Lite` test 分片。
test 分片与公共榜单重叠——本仓库只做内部 baseline-vs-gated 相对对比，不提交榜单；若介意，
可改用 `SWE-bench_Verified` 等非重叠分片（数据列结构一致即可）。

### 7.2 批量运行：`scripts/run_swebench_batch.py`

对任务清单逐实例执行 baseline / gated Agent 运行并接官方评分，追加写 `results.csv`。
PowerShell 请用**单行**执行（下方示例已展平）；bash 可用 `\` 续行。任务清单/数据集在
`.pytest_tmp/bench_data/` 下，命令里要带该前缀：

```powershell
python scripts/run_swebench_batch.py --tasks .pytest_tmp/bench_data/tasks_dev12.json --dataset .pytest_tmp/bench_data/swebench_dev.json --agent-python .pytest_tmp/venv312/Scripts/python.exe --agent-script .pytest_tmp/bench_data/run_agent.py --grade-python .pytest_tmp/sweb_venv/Scripts/python.exe --grade-script .pytest_tmp/bench_data/grade.py --workdir-root .pytest_tmp/bench_data/wd --venv-map .pytest_tmp/bench_data/venv_map.json --modes baseline,gated --max-turns 60 --prepare-workdir --skip-existing --outdir .pytest_tmp/bench_data/results
```

前置条件与注意点（沿用第六节踩坑结论）：

- 每个实例的官方 Docker 评测镜像须已拉取并 tag 回官方名（Docker Hub 不通时经镜像站拉取，
  如 `docker.1panel.live`）；`run_evaluation` 会按 `image` 列自动找镜像。
- 每个仓库需要一个装好依赖的 venv（py3.12 + pytest；marshmallow 2.x 需 setuptools 兼容层，
  pydicom 2.1 需 `setuptools<81` 提供 `pkg_resources`）；多仓库用 `--venv-map` 传 JSON 文件，
  例如 `{"marshmallow-code/marshmallow": ".../mm_venv/Scripts/python.exe",
  "pydicom/pydicom": ".../pd_venv/Scripts/python.exe"}`（键支持 repo 或 instance_id）。
- 预检与断点续跑：默认先检查 venv 与 Docker 镜像，缺失的 (实例, 组别) 直接跳过并打印缺什么，
  不会白跑 Agent（`--no-preflight` 关闭）；`results.csv` 已记录的行默认跳过（`--rerun` 强制重跑）。
- `--prepare-workdir` 用 `git init + fetch --depth 1 <base_commit>` 自动建工作区；数据集与
  harness 写文件 CRLF→LF 修复、`PYTHONUTF8=1` 见第六节。
- 单实例可加 `--instance <iid>` 调试。
- `--venv-map` 指向 JSON（键为 repo 或 instance_id，值为该环境 python 绝对/仓库相对路径）；
  每新备好一个仓库的 venv 就往 JSON 加一行再重跑，未映射的行会被预检跳过。
  本仓库示例见 `.pytest_tmp/bench_data/venv_map.json`（marshmallow-1343/1359、pydicom-1256）。

### 7.3 新增实例的环境准备（Windows PowerShell 操作序列）

批量脚本只会运行“venv 已映射 + Docker 镜像已就绪”的 (实例, 组别)。给一个新实例开跑的操作：

```powershell
# 1) 共享 bench venv（一次性；仓库代码从工作区直接导入，venv 只需 pytest + 常见依赖）
python -m venv .pytest_tmp/bench_data/bench_venv
.pytest_tmp/bench_data/bench_venv/Scripts/python.exe -m pip install -U pip
.pytest_tmp/bench_data/bench_venv/Scripts/python.exe -m pip install pytest numpy pandas "setuptools<81"

# 2) 往 .pytest_tmp/bench_data/venv_map.json 追加一行（示例：sqlfluff-1517）
#    "sqlfluff__sqlfluff-1517": ".pytest_tmp/bench_data/bench_venv/Scripts/python.exe"

# 3) 拉官方评测镜像（直连 Docker Hub 不通时经镜像站拉取后 tag 回官方名）
docker pull docker.1panel.live/swebench/sweb.eval.x86_64.sqlfluff_1776_sqlfluff-1517:latest
docker tag docker.1panel.live/swebench/sweb.eval.x86_64.sqlfluff_1776_sqlfluff-1517:latest swebench/sweb.eval.x86_64.sqlfluff_1776_sqlfluff-1517:latest

# 4) 可选：先在工作区确认测试可收集（避免 Agent 白跑），测试目录以仓库为准
python -m pytest --collect-only -q

# 5) 重跑 7.2 的批量命令——已完成自动跳过，新备好的实例开始执行
```

各仓库要点：

- 纯 Python（sqlfluff / astroid）：bench_venv 即可；astroid 测试还依赖 pylint 等，按报错补装。
- pvlib：需要 numpy / pandas / scipy（bench_venv 已含前两者，缺 scipy 补装）。
- pyvista：依赖 VTK，环境重，建议放最后或从任务清单剔除。
- pydicom-1256 已用 pd_venv（py3.12 + numpy + setuptools<81）；同版本其他实例可复用它，
  版本不同需确认依赖后再映射。

### 7.4 Scale-10 真实双组实测（2026-09-09，SWE-bench Lite test 分片）

把 §6 的 3 实例小样本扩到 10 实例。数据源 `SWE-bench/SWE-bench_Lite` **test 分片**
（`swebench_test.json`，300 实例），按仓库分层抽 10 个：django×4 / sympy×4 / flask×1 /
seaborn×1（`tasks_scale10.json`）。与 §6 同环境：Agent 为 DeepSeek `deepseek-v4-flash`，
上限 60 轮；每仓库独立 venv；官方 eval 镜像 + swebench 5.0.2 `run_evaluation` 打分。
冒烟 4 实例（django-10914/10924、sympy-11400/11870）先跑，其余 6 实例于当日续跑完成，
共 10 实例 × 2 组 = 20 行，存 `results_scale10/results.csv`。

| 实例 | baseline resolve | gated resolve | gated 拦截 / 终态 |
|---|---|---|---|
| django-10914 | 1（60轮/469s） | 0（60轮/581s） | 3 / 阶段2 |
| django-10924 | 0（空补丁） | 0（60轮/484s） | 3 / 阶段2 |
| sympy-11400 | 0（60轮/285s） | 0（32轮/81s） | 0 / 阶段6交付 |
| sympy-11870 | 0（60轮/291s） | 0（60轮/408s） | 1 / 阶段4 |
| django-11001 | 0（空补丁） | 0（空补丁） | 1 / 阶段2 |
| django-11019 | 0（空补丁） | 0（空补丁） | 4 / 阶段2 |
| sympy-11897 | 0（空补丁） | 0（空补丁） | 1 / 阶段2 |
| sympy-12171 | 0（60轮/637s） | 0（60轮/512s） | 1 / 阶段5 |
| flask-4045 | 0（60轮/296s） | 0（60轮/210s） | 0 / 阶段6交付 |
| seaborn-2848 | 0（60轮/269s） | 0（60轮/336s） | 0 / 阶段6交付 |

汇总：**baseline 1/10（10%）resolve；gated 0/10（0%）**；gated 全程拦截共 14 次；
gated 有 3 个实例完整走到阶段 6 并交付（sympy-11400、flask-4045、seaborn-2848），
其中 flask-4045 双组都产出非空补丁但均未过隐藏测试。

结论与要点：
- 门禁对“过程合规”有实质约束：gated 组只有走到阶段 6 才算放行，拦截集中在 spec/测试
  不达标的实例（多数卡在阶段 2），未出现 baseline 那种 60 轮空转后直接空补丁的情况被放行。
- 双组空补丁出现在同一批难点（django-11001/11019、sympy-11897），且 transcript 显示模型
  60 轮内反复侦查（git log / python 探测）未落盘修改，属模型×任务难度问题，非门禁副作用；
  sympy-11400 gated 32 轮即完成阶段 6，说明门禁不是简单“拖慢”。
- 数据质量修正：批量期间发现 `run_agent.py` 提取补丁用 `git diff`（仅未暂存），
  seaborn-2848 gated 因 Agent `git add` 后提取为空而误记 empty_patch；已改为
  `git diff HEAD` 并复核该实例（恢复补丁经官方 harness 重评分仍 0，行内 diff/note 已更正）。
- 边界观测：seaborn-2848 gated 走到阶段 6 后，最后一轮 pytest 实为收集错误（exit 4）；
  说明“阶段计数到 6”≠“交付前最终测试全绿”，与防线 4（事后行为审计）的动机一致，留待后续。
- 局限：60 轮上限 + flash 模型导致 resolve 偏低，baseline 与 gated 绝对数值均不具榜单意义，
  只用于**同条件下相对对比**；建议下一步换更强模型 / 提高轮数，或切 Verified 分片再测。

