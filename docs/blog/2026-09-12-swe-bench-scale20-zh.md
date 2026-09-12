---
title: "给编码 Agent 装一道阶段门禁：SWE-bench Lite 从 90% 到 100%（n=20）"
date: 2026-09-12
tags: [AI Agent, 软件工程, SWE-bench, 评测, 开源]
---

# 给编码 Agent 装一道阶段门禁：SWE-bench Lite 从 90% 到 100%（n=20）

**一句话结论**：在官方 SWE-bench 评测容器内，同模型同预算、20 个配对实例，无门禁 baseline
18/20（90%），加门禁 gated **20/20（100%）**；累计拦截 78 次，0 空补丁，0 例拖累；
代价是平均墙钟耗时约 1.8 倍。**n=20 只用于同条件对照，不是榜单结论**，原始 CSV 与复算命令都在仓库里。

## 实验设置

- 数据集：`SWE-bench/SWE-bench_Lite` 的 `test` 分片，20 个实例（django 4 / sympy 4 /
  astropy 2 / matplotlib 2，另加 seaborn、flask、requests、xarray、pylint、pytest、
  scikit-learn、sphinx）。
- 组别：`baseline`（无门禁）与 `gated`（阶段门禁），同实例配对。
- 模型与预算：同一模型、同一提示词、同一温度，每实例最多 60 轮工具调用。
- 环境：Agent 跑在官方 `swebench/sweb.eval.x86_64.*` 镜像内；评分在宿主用官方
  `swebench==5.0.2` harness 跑 `/eval.sh`（FAIL_TO_PASS / PASS_TO_PASS）。

## 结果

| 组别 | n | resolved | 比例 | Wilson 95% | 拦截 | 阶段 6 且交付全绿 | 空补丁 | 平均耗时 |
|---|---|---|---|---|---|---|---|---|
| baseline | 20 | 18 | 90% | [69.9%, 97.2%] | 0 | — | 0 | 262s |
| gated | 20 | 20 | 100% | [83.9%, 100.0%] | 78 | 14 | 0 | 462s |

配对看：baseline 失败、gated 成功的实例是 `matplotlib-22711` 与 `sympy-11400`，反方向 0 例。
两组 Wilson 区间有重叠——**这是方向性结论，不是统计显著**，所以我们才要继续扩样本。

## 最有意思的部分：第一轮 gated 只有 65%

首轮 gated 只拿到 **13/20**，6 个实例**永久卡在阶段 2**（测试校验）再也推不动。根因都在门禁侧：

1. **语言误判**：探测逻辑「标志文件先到先得」，而 django / sphinx 根目录有 `package.json`
   （前端或文档工具链），于是 Python 仓库被判成 `javascript`，用 JS 启发式校验 `.py` 测试
   → 报「断言关键字不足」。修复：多标志命中时按**源文件数**消歧（跳过 `node_modules`、
   `.venv`、`build` 等），无法消歧再回退原优先级。
2. **存量测试白嫖**：`validate_tests` 扫描整个仓库（几十年历史夹具），Agent 不写新测试也能过。
   修复：新增 `stage2_test_scope: changed`（默认），只校验相对 `HEAD` 新增/修改的测试文件，
   非 Git 或工作区干净时自动回退。

修复后 6 个卡死实例 **resolved 0 → 1**，gated 从 **13/20 变 20/20**。回归测试用 django / sphinx
的真实目录布局复现，并覆盖「拿存量测试蒙混」的绕过用例。

## 两个会让评测数据整体失真的 harness 陷阱

1. **陈旧补丁被当成本轮结果评分**：Agent 容器秒退时，上一轮的 `model_patch.diff` 还在挂载目录里，
   驱动直接拿它评分——20 行里一度有 14 行是假数据。修复：运行前清空同名 label 目录 +
   校验补丁 mtime 晚于本轮开始。
2. **并发写坏共享依赖卷**：Docker Desktop 卷挂载下 `mkdir` 锁不互斥，并发
   `pip install --target` 把门禁依赖卷写坏（实测缺 `typing_extensions`）。修复：bootstrap 改成
   「`rm -rf` → 安装 → `import` 校验 → 才落 `.ready`」。

## 门禁的代价与它保证了什么

- 代价：平均多 6.7 轮、墙钟约 1.8 倍。
- 保证：每个交付都走到阶段 6，且最近一次测试「全绿且晚于最后一次源码修改」（`delivery_clean()`）；
  不会拿未验证的证据交付。
- 需要说清的一点：6 个 gated 实例在阶段 3/4 就耗尽 60 轮预算，门禁**没有放行交付**，
  但官方评测只看补丁，所以这些补丁仍判定 resolved。「门禁拦住流程」与「评测通过」是两件事。

## 复现

```bash
python benchmarks/swebench/analyze_results.py \
  --results benchmarks/swebench/results/scale20_lite_container.csv
```

完整报告：`docs/benchmarks/swe-bench-scale20.md`；仓库：<https://github.com/Xuqing0415/phase-barrier>。

## 知乎 / V2EX 发布备注

- 标题可换成《我们给编码 Agent 加了道门禁，SWE-bench 从 90% 到 100%——但第一轮只有 65%》，用
  「第一轮 65%」的自我修正做钩子，比单说 100% 更可信。
- 正文保留 n=20 与 Wilson 区间重叠的说明，避免被质疑小样本；把「复现命令」放在显眼位置。
