# SWE-bench 真实评测工具链（2026-09 入库）

本目录把「在真实 SWE-bench 实例上跑 phase-barrier 双组对照」所需的脚本固化进仓库，
解决此前评测脚本只存在于本地临时目录（`.pytest_tmp/bench_data/`）、结果不可复现的问题。

## 组成

| 文件 | 运行位置 | 职责 |
|---|---|---|
| `run_agent.py` | 官方评测镜像**容器内** | DeepSeek 工具循环修 bug；gated 模式接入 `AntiShortcutSkill` 阶段门禁；产出 `model_patch.diff` + `stats.json` + `PB_*` 标记 |
| `grade.py` | **宿主**（评分 venv） | 用官方 `swebench==5.0.2` harness 在评测镜像里应用补丁、跑 `/eval.sh`、按 FAIL_TO_PASS / PASS_TO_PASS 判定 resolved |
| `prepare_dataset.py` | 宿主（评分 venv） | 从 canonical 数据集导出实例行，可按「本机已缓存镜像」过滤，产出 `dataset.json` + `tasks.json` |
| `gold_check.py` | 宿主（评分 venv） | **自检**：用数据集里的 gold patch 打分，必须 resolved=1 —— 跑 Agent 评测前先过这一关 |
| `regrade_cross_shard.py` | 宿主（评分 venv） | 把已产出的补丁在另一分片（如 Verified）上重新评分，不重跑 Agent |
| `analyze_results.py` | 任意（只读 CSV） | 把 `results.csv` 汇总成可复算统计：resolve 率 + Wilson 95% 区间、拦截/阶段 6/交付全绿/空补丁、逐实例「被门禁救回 / 拖累」清单 |

编排由仓库既有的 `scripts/run_swebench_batch_container.py` 负责；分层抽样由
`scripts/select_swe_tasks.py` 负责；批量拉取官方评测镜像由
`scripts/pull_swebench_images.py` 负责（支持镜像站拉取后 tag 回官方名）。

## 前置条件

- Docker 可用，且目标任务对应的官方镜像已缓存（`swebench/sweb.eval.x86_64.*:latest`）。
  走镜像站拉取后 `docker tag` 回官方名亦可。
- 一个装了官方 harness 的 venv：`python -m venv .pytest_tmp/sweb_venv && .pytest_tmp/sweb_venv/Scripts/python.exe -m pip install "swebench==5.0.2"`。
- 宿主环境变量 `DEEPSEEK_API_KEY`；可选 `PB_DS_MODEL`（默认 `deepseek-v4-flash`）。

镜像的 base conda 不含 `pydantic / PyYAML / structlog`，编排器会在容器内用命名卷
`pb_gate_deps` 按门禁解释器的小版本号缓存一份依赖（首次 pip 安装，后续复用）。

## 三步跑通

```bash
# 1) 导出与本机镜像匹配的实例（Verified 为 canonical 分片，含 eval_script 列）
HF_ENDPOINT=https://hf-mirror.com .pytest_tmp/sweb_venv/Scripts/python.exe \
  benchmarks/swebench/prepare_dataset.py \
  --dataset SWE-bench/SWE-bench_Verified --split test \
  --local-images-only --out-dir .pytest_tmp/bench_data/verified

# 2) 分层抽样（含 must-repo 配额）
python scripts/select_swe_tasks.py --dataset .pytest_tmp/bench_data/verified/dataset.json \
  --count 20 --must-repo django/django sympy/sympy --per-repo 2 \
  --exclude pydicom__pydicom-1413 --out .pytest_tmp/bench_data/verified/tasks_scale20.json

# 3) 双组评测（容器内 Agent + 官方评分）
python scripts/run_swebench_batch_container.py \
  --tasks .pytest_tmp/bench_data/verified/tasks_scale20.json \
  --dataset .pytest_tmp/bench_data/verified/dataset.json \
  --grade-python .pytest_tmp/sweb_venv/Scripts/python.exe \
  --results .pytest_tmp/bench_data/results/scale20.csv \
  --log-dir .pytest_tmp/bench_data/container_logs \
  --modes baseline,gated --max-turns 60 --concurrency 3 --skip-existing
```

`--skip-existing` 按 `(instance_id, mode)` 断点续跑，中断后重跑同一条命令即可。
已入库的两份评测数据见 [`results/`](results/)（含复算命令与实测口径说明）。

## 跑之前先自检（强烈建议）

```bash
python benchmarks/swebench/gold_check.py \
  --dataset .pytest_tmp/bench_data/lite/dataset.json --limit 3 \
  --grade-python .pytest_tmp/sweb_venv/Scripts/python.exe \
  --out-dir .pytest_tmp/bench_data/eval_gold
```

gold patch 打不出 `resolved=1` 说明评分链路本身有问题，此时 Agent 的所有 `resolved=0` 都不可信。
本仓库实测踩过一次：Windows 上 `eval.sh` 被写成 CRLF，容器里 `set -e` / `conda activate`
全部解析失败，表现为「PASS_TO_PASS 98/98 全挂」的假阴性（`grade.py` 已用 `force_lf_writes()` 修掉）。

## 接口契约

`run_agent.py` 必须打印（编排器据此填 CSV）：

```
PB_TURNS=<轮数>
PB_DIFF_CHARS=<补丁字符数>
PB_GATE_INTERCEPTS=<门禁拦截次数>
PB_GATE_FINAL_STAGE=<最终阶段号，baseline 为空>
PB_GATE_COMPLETED=<是否走到阶段 6：1/0>
```

`grade.py` 退出码：`0` = resolved，`2` = unresolved，`3` = 评分出错；打印
`PB_RESOLVED=1|0`。

## 已知坑

- `grade.py` 不能用 `swebench` 的 `rewrite_reports=True`——那表示「只重新格式化已有报告」，
  会跳过真正评测并报 `test_output.txt does not exist`。
- 数据集必须用 canonical 的 `SWE-bench/SWE-bench_Verified`；`princeton-nlp/SWE-bench_Verified`
  缺 `eval_script` 列，会让 `make_test_spec` 抛 `KeyError: 'eval_script'`。
- HuggingFace 直连在本机 TLS 失败，用 `HF_ENDPOINT=https://hf-mirror.com`。
- 补丁提取用 `git add -A && git diff HEAD`，并把 `.agent_gate/` 与 `spec.md` 写进
  `.git/info/exclude`，避免门禁过程产物混进提交给评测的补丁。
- gated 模式的阶段 2/3 校验会扫描整个存量仓库的测试与源码文件，因此配置里放宽为
  `require_assert_per_test: false` + `min_test_functions: 1`（仓库历史测试不保证每个都有断言）。
