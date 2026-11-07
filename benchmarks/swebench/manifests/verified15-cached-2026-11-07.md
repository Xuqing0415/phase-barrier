# Verified 缓存镜像子样本（2026-11-07）

## 目的

在预注册的 12 仓库 Verified-15 镜像尚未全部就绪时，先用本地已缓存的
Verified 实例跑一批 15 实例对照，回答“门禁是否降低正常 Agent 的解决率”。
预注册样本将在镜像拉取完成后作为第二批验证补充。

## 选择过程

- 数据源：`SWE-bench_Verified` test split，500 个实例。
- 本地可用性筛选：只保留 `image` 字段能在本地 `docker images` 中命中的实例，
  得到 17 个候选，覆盖 7 个仓库。
- 其余 483 个实例因本地无镜像被排除，原因不是任务难度或结果。
- 从 17 个候选中用
  `scripts/select_swe_tasks.py --count 15 --balance-difficulty --seed 7`
  选出 15 个。
- 配额按仓库 largest-remainder 分配；仓内按难度轮转，起始桶由
  `sha256(seed:repo)` 偏移。

## 选择偏差（必须随结果一起报告）

- 这是**缓存镜像子样本**，不是随机样本，也不是预注册的 12 仓库样本。
- 覆盖 7 个仓库：astropy、django、matplotlib、seaborn、flask、requests、scikit-learn。
- 选择受本地镜像缓存影响，可能偏向过去跑过 Lite/Verified 实验的仓库。
- 预注册的 12 仓库样本（`verified_15_offset.json`）将在 12 个缺失镜像就绪后补跑。

## 实际分布

- 实例数：15
- 仓库分布：astropy 3、django 5、matplotlib 2、seaborn 2、flask 1、requests 1、scikit-learn 1
- 难度分布：`<15 min fix` 7、`15 min - 1 hour` 7、`1-4 hours` 1、`>4 hours` 0
- 按配额排除：`astropy__astropy-13033`、`django__django-10880`
  （`reason=not_selected_by_quota`）

## 复现

```bash
python scripts/select_swe_tasks.py \
  --dataset .pytest_tmp/verified15/cached17_dataset.json \
  --count 15 --balance-difficulty --seed 7 \
  --out .pytest_tmp/verified15/cached15.json
```

`selection_meta.excluded` 记录了被配额排除的实例；完整候选集来自
`.pytest_tmp/verified15/local_images.txt` 与 `dataset.json` 的交集。
