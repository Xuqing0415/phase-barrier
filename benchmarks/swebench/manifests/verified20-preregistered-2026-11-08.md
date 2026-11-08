# Verified 预注册第二批 20 实例（2026-11-08）

## 目的

在不依赖镜像可用性的前提下，先固定第二批 Verified 样本，避免跑完后按结果换样本。
本批用于门禁的回归检测、成本量化和跨批方向一致性，不用于宣称统计显著提升。

## 固定样本设计

- 数据源：`SWE-bench/SWE-bench_Verified` test split，500 个实例。
- 选择：`seed=7`，12 个仓库全覆盖，显式仓库配额，仓内按难度轮转。
- 原始建议配额表合计 19 个；第 20 个名额给池最大的 `django/django`，因此
  django 为 4 而非 3。这是唯一偏离建议表的地方，选择前已固定。
- 难度分布：`<15 min fix` 8、`15 min - 1 hour` 6、`1-4 hours` 6、`>4 hours` 0。
- 排除前两批已执行实例：28 个（Lite-20 与缓存-15 的并集，剔除下面两个强制复用项）。
- 不可避免的重叠：`mwaskom__seaborn-3069`、`pallets__flask-5014`。
  Verified 中这两个仓库没有剩余可替代实例；为保留 12 仓库覆盖而复用。
  **因此本批不能称为与前两批完全独立，报告中必须单列这两个实例。**
- **有效独立样本 n=18**（20 减去 2 个复用实例）；复用实例单列，不计入第二批独立统计效力。
- 复用实例第二次运行与第一次的一致性单独记录：一致可作为跨批次稳定性证据；不一致则说明 Agent 存在非确定性，不能把差异全部归因于门禁。

## 仓库配额

| 仓库 | 配额 |
|---|---:|
| django/django | 4 |
| sympy/sympy | 3 |
| scikit-learn/scikit-learn | 2 |
| sphinx-doc/sphinx | 2 |
| matplotlib/matplotlib | 2 |
| astropy/astropy | 1 |
| mwaskom/seaborn | 1 |
| pallets/flask | 1 |
| psf/requests | 1 |
| pydata/xarray | 1 |
| pylint-dev/pylint | 1 |
| pytest-dev/pytest | 1 |

## 实例清单

| instance_id | repo | difficulty |
|---|---|---|
| astropy__astropy-14369 | astropy/astropy | 1-4 hours |
| django__django-14631 | django/django | 1-4 hours |
| django__django-16333 | django/django | <15 min fix |
| django__django-16631 | django/django | 1-4 hours |
| django__django-16901 | django/django | 15 min - 1 hour |
| matplotlib__matplotlib-23476 | matplotlib/matplotlib | <15 min fix |
| matplotlib__matplotlib-24970 | matplotlib/matplotlib | 15 min - 1 hour |
| mwaskom__seaborn-3069 | mwaskom/seaborn | 15 min - 1 hour |
| pallets__flask-5014 | pallets/flask | <15 min fix |
| psf__requests-2317 | psf/requests | <15 min fix |
| pydata__xarray-3993 | pydata/xarray | 1-4 hours |
| pylint-dev__pylint-4970 | pylint-dev/pylint | <15 min fix |
| pytest-dev__pytest-7205 | pytest-dev/pytest | <15 min fix |
| scikit-learn__scikit-learn-15100 | scikit-learn/scikit-learn | 15 min - 1 hour |
| scikit-learn__scikit-learn-25102 | scikit-learn/scikit-learn | 1-4 hours |
| sphinx-doc__sphinx-8593 | sphinx-doc/sphinx | 15 min - 1 hour |
| sphinx-doc__sphinx-9281 | sphinx-doc/sphinx | <15 min fix |
| sympy__sympy-13480 | sympy/sympy | <15 min fix |
| sympy__sympy-17630 | sympy/sympy | 1-4 hours |
| sympy__sympy-18211 | sympy/sympy | 15 min - 1 hour |

## 镜像状态（选择时快照）

- 选择不读取镜像缓存，不按可用性换样本。
- 固定清单生成时本地仅 3/20 镜像就绪，17 个缺失。
- `docker.1ms.run` 对缺失 tag 的 manifest 探测成功；`docker.1panel.live` 超时。
  因此拉取改用 `docker.1ms.run`，不在 1panel 上继续等待。

## 分析计划

- 主结论：三批独立对照中均不出现 `baseline-only` 实例，即 gated 没有比 baseline 更差。
- 次结论：记录轮数、耗时、补丁大小、拦截次数和交付全绿率，量化门禁成本。
- 方向一致性：三批的 `gated - baseline` 差值均不得为负。
- n=20 不保证 McNemar 双侧 `p<0.05`；报告不得宣称“显著提升”。
- 三批分别报告，不合并成 pooled 成功率；选择偏差和两个复用实例必须披露。

## 复现

```bash
python scripts/select_swe_tasks.py \
  --dataset .pytest_tmp/verified15/dataset.json \
  --count 20 --seed 7 \
  --exclude-file .pytest_tmp/verified20/excluded_prior_instances.txt \
  --repo-quota \
    django/django=4 sympy/sympy=3 \
    scikit-learn/scikit-learn=2 sphinx-doc/sphinx=2 matplotlib/matplotlib=2 \
    astropy/astropy=1 mwaskom/seaborn=1 pallets/flask=1 psf/requests=1 \
    pydata/xarray=1 pylint-dev/pylint=1 pytest-dev/pytest=1 \
  --out .pytest_tmp/verified20/verified_20_preregistered.json
```

完整可运行任务清单（含 gold patch，仅保存在本机忽略目录）：
`.pytest_tmp/verified20/verified_20_preregistered.json`。
仓库内固定清单（不含 gold patch）：
`benchmarks/swebench/manifests/verified20-preregistered-2026-11-08.json`。
