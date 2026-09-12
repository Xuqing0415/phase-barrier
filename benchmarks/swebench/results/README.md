# SWE-bench 实测数据（已入库）

这些 CSV 是文档 [docs/tutorials/swe-bench-real.md](../../../docs/tutorials/swe-bench-real.md)
里 §8 / §9 数字的**原始出处**：行数与 `resolved` 可直接复算，不需要重跑 Agent。

## 文件

| 文件 | 内容 | 生成方式 |
|---|---|---|
| `scale20_lite_container.csv` | Scale-20（SWE-bench Lite `test` 分片，20 实例 × baseline/gated = 40 行） | `scripts/run_swebench_batch_container.py`（官方 eval 镜像内跑 Agent + 宿主侧官方 harness 评分） |
| `verified_regrade.csv` | 上表补丁在 SWE-bench Verified 分片上的重新评分（4 实例 × 2 组 = 8 行） | `benchmarks/swebench/regrade_cross_shard.py`（不重跑 Agent，只换分片打分） |

CSV 列见 `run_swebench_batch_container.py` 的 `COLUMNS`：`instance_id, repo, mode, resolved,
turns, seconds, diff_chars, gate_intercepts, gate_final_stage, gate_completed, note`
（`gate_*` 仅 gated 组有值；`gate_completed` = `State.delivery_clean()`，即
「阶段 6 + 最近一次测试全绿 + 晚于最后一次源码/测试变更」）。

## 汇总（可复算）

```bash
python - <<'PY'
import csv, io, statistics
rows = list(csv.DictReader(io.open("benchmarks/swebench/results/scale20_lite_container.csv", encoding="utf-8")))
for mode in ("baseline", "gated"):
    g = [r for r in rows if r["mode"] == mode]
    res = sum(1 for r in g if r["resolved"] == "1")
    print(mode, "n=", len(g), "resolved=", res,
          "拦截=", sum(int(r["gate_intercepts"] or 0) for r in g),
          "阶段6=", sum(1 for r in g if r["gate_final_stage"] == "6"),
          "交付全绿=", sum(1 for r in g if r["gate_completed"] == "1"),
          "平均轮数=", round(statistics.mean(int(r["turns"]) for r in g if r["turns"]), 1),
          "平均秒=", round(statistics.mean(float(r["seconds"]) for r in g if r["seconds"])))
PY
```

| 组别 | n | resolved | 拦截 | 阶段6 | 交付全绿 | 平均轮数 | 平均耗时 |
|---|---|---|---|---|---|---|---|
| baseline | 20 | 18（90%） | 0 | — | — | 43.1 | 262s |
| gated | 20 | 20（100%） | 78 | 14 | 14 | 49.8 | 462s |

## 口径与已知限制

- 同一模型 `deepseek-v4-flash`、同一 60 轮预算、同一官方镜像环境；n=20 只用于**同条件相对对比**，
  不构成榜单结论。
- gated 数据是**修复阶段 2 存量仓库假阳性之后**重跑的；修复前同批量 gated 为 13/20（65%），
  6 个实例永久卡在阶段 2（详见教程 §8.3）。
- `seconds` 是容器内 Agent 自身的墙钟耗时；宿主休眠会把它和驱动的看门狗一起拉长，
  这类行已按 `container_timeout` 剔除后重跑，入库行 `note` 全为 `graded`。
- 复现需要：本机已缓存对应官方 eval 镜像、`DEEPSEEK_API_KEY`、宿主 `.pytest_tmp/sweb_venv`
  （`swebench==5.0.2` + docker SDK）。
