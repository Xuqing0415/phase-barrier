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