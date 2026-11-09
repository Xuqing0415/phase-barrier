# Verified 预注册-20：执行准备与镜像覆盖

本文是执行前的可用性记录及报告模板，不是实验结果。预注册 20 实例清单保持不变。

## 可用性快照：2026-11-08 20:35:55（北京时间）

- 预注册：20 实例、12 仓库；有效独立样本计划为 18，另有 2 个复用实例。
- 精确 image inspect：14 个就绪、6 个缺失、0 个环境错误。
- 当前可用镜像覆盖 10 个仓库；scikit-learn 和 xarray 的覆盖暂时缺失。
- 如果最终只能执行当前这 14 个：独立实例为 12，复用实例为 2；独立实例覆盖 8 个仓库，总覆盖 10 个仓库。
- 实际执行尚未开始。以上 14/12/10 是条件情景，不能写成已执行结果；换源补齐后应重新计算。

| 仓库 | 预注册配额 | 就绪 | 缺失 | 对当前覆盖的影响 |
|---|---:|---:|---:|---|
| astropy/astropy | 1 | 1 | 0 | 齐备 |
| django/django | 4 | 4 | 0 | 齐备 |
| matplotlib/matplotlib | 2 | 1 | 1 | 覆盖保留、样本不齐 |
| mwaskom/seaborn | 1 | 1 | 0 | 齐备 |
| pallets/flask | 1 | 1 | 0 | 齐备 |
| psf/requests | 1 | 1 | 0 | 齐备 |
| pydata/xarray | 1 | 0 | 1 | 覆盖暂缺 |
| pylint-dev/pylint | 1 | 1 | 0 | 齐备 |
| pytest-dev/pytest | 1 | 1 | 0 | 齐备 |
| scikit-learn/scikit-learn | 2 | 0 | 2 | 覆盖暂缺 |
| sphinx-doc/sphinx | 2 | 1 | 1 | 覆盖保留、样本不齐 |
| sympy/sympy | 3 | 2 | 1 | 覆盖保留、样本不齐 |

6 个缺失实例均为独立实例：

- `matplotlib__matplotlib-24970`（matplotlib/matplotlib）
- `pydata__xarray-3993`（pydata/xarray）
- `scikit-learn__scikit-learn-15100`（scikit-learn/scikit-learn）
- `scikit-learn__scikit-learn-25102`（scikit-learn/scikit-learn）
- `sphinx-doc__sphinx-8593`（sphinx-doc/sphinx）
- `sympy__sympy-17630`（sympy/sympy）

## 一小时复查与换源决定

- 第二次检查：2026-11-08 21:35:57（北京时间），仍为 14/20 就绪、6 缺失、0 个环境错误。
- 与 20:35:55 基线相比，经过 60 分钟后净增量为 0，按既定规则切换 LogicStar。
- LogicStar 官方镜像索引覆盖全部 20 个目标；压缩包下载链路已验证。下载校验、加载和本地 tag 确认属于后续步骤，不能用远端覆盖代替本地就绪。
- 预注册实例、配额和独立统计口径保持不变；换源仅改变运行时镜像来源。

## 5 实例链路确认候选

清单：`benchmarks/swebench/manifests/link_check_5.json`。仅用于容器、门禁、评分链路验证，不能作为正式样本替换或结果筛选依据。

| 实例 | 仓库 | 选择时镜像状态 |
|---|---|---|
| django__django-16901 | django/django | ready |
| sympy__sympy-18211 | sympy/sympy | ready |
| sphinx-doc__sphinx-9281 | sphinx-doc/sphinx | ready |
| matplotlib__matplotlib-23476 | matplotlib/matplotlib | ready |
| astropy__astropy-14369 | astropy/astropy | ready |

scikit-learn 的两个目标均未就绪，因此第五个仓库使用 astropy。候选中的 5 个均为独立实例。先等待二次测量与换源决策；镜像加载与容器跑批不得并行争抢 Docker。

## 第二批：Verified 预注册-20（跑完后填写）

- 预注册清单：20 实例，12 仓库。
- 实际执行：N 实例（因镜像不可用缺失 M 个；仅当没有其他跳过原因时 M = 20 − N）。
- 有效独立样本：N − R；R 为实际执行的复用实例数。两项均执行时 R = 2，即 N − 2。
- 仓库覆盖：实际 K 个（预注册 12 个）。
- 复用实例：seaborn-3069、flask-5014，结果单列，不计入独立统计。
- N 按去重 instance_id 统计，不按 baseline/gated 两组行数统计；两组都完整执行并评分的独立配对数另列，不能把缺失评分当作 unresolved。
- 另外记录每实例轮数、耗时、拦截次数、补丁大小、gate_completed 及官方评分 note。

## 复用实例（不计入独立统计）

| 实例 | 上次结果 | 本批结果 | 是否一致 |
|---|---|---|---|
| seaborn-3069 | 缓存-15 正式批：gated resolved（54 轮） | 待运行 | 待比较 |
| flask-5014 | 缓存-15 正式批：gated resolved（39 轮） | 待运行 | 待比较 |

上次正式批结果以 `benchmarks/swebench/results/verified15_cached.csv` 为准。
早先链路检查另有记录：`pilot5_results.csv` 中 seaborn-3069 的 gated 为 unresolved（60 轮），
`pilot_one_results.csv` 中 flask-5014 的 gated 为 resolved（54 轮）。这些是不同运行，
不能把早先 pilot 的轮数或结果标成缓存-15 正式批结果。
一致性单独讨论；不一致时记录非确定性及环境差异，不直接归因于门禁。
正式报告继续三批并列，不做 pooled 成功率统计。

## 跨批实例去重核验（2026-11-09）

按已提交的 `scale20_lite_container.csv`、`verified15_cached.csv` 和本批预注册清单的
`instance_id` 集合核对，Lite-20 与缓存-15 共有 4 个实例：

- `astropy__astropy-12907`
- `astropy__astropy-14182`
- `django__django-10914`
- `scikit-learn__scikit-learn-10297`

因此，“一次新的 Agent 运行”不等于“一个跨批新增独立实例”。各批仍分别报告原有
配对结果，但跨批新增实例数依次为 20、11、18；若本批 20 个全部执行，三批去重后
共 49 个实例，不能写成累计独立 n=53。这是实例身份核验，不是合并成功率或合并检验。
本批的 20 个预注册实例、12 仓库覆盖以及 18 个新增独立实例 + 2 个复用实例口径均不变。

## 镜像补齐与容器预检（2026-11-09）

- `mirror-downloader@1.0.3` 通过 hf-mirror，以 16 条配置连接下载
  `LogicStar/SWE-Bench-Verified-Compressed` 的 `layout.tar.zst`。
  文件为 5,401,822,437 字节，SHA256 已核验为
  `e44d0f4271ee2e32a4b64bb9a369be99bf2fa1ba689a6f3f9628d9245ae5272a`。
- 仅导入固定 20 个目标，逐项核对 OCI config、RootFS diff IDs 和运行配置，
  20/20 就绪。上游 `load.py` 使用 Podman；本机采用保留 config/layer 原始字节的
  OCI → Docker-save 转换。Docker 29 的 image ID 不能直接当作 OCI config digest。
- Docker 重启恢复完成，重启后再次确认 20/20 镜像与导入前一致。
  原有 PostgreSQL 容器也已恢复并通过健康检查。
- Agent 和官方评分器共用运行时数据集中的 `logicstar/` 镜像名称；预注册清单未改，
  未将压缩镜像重新标记为官方 namespace。
- 源码审计：17 个镜像的 HEAD tree 与 `base_commit` tree 相同。
  Astropy-14369 和 Sphinx-9281 的环境适配文件与对应本地官方镜像 tree 完全一致。
  Sphinx-8593 没有本地官方镜像可直接比对；其 `setup.py` 依赖约束和 `tox.ini`
  测试输出参数的增删，与官方 Sphinx-9281 的适配完全相同，没有其他文件差异。
  此项仅核验了环境适配，不声称该实例整个镜像与官方逐字节相同。
- 五个固定候选均通过容器预检：工作区干净、源码 tree 符合上述审计、
  Agent 数据仅含四个允许字段、输入挂载只读、输出可写，六个越界读写尝试均被拒绝。
  保留上游环境适配，Agent 与评分器使用相同初始源码树。

可复核的环境摘要保存在 `benchmarks/swebench/results/verified20_environment.json`。
本节是环境就绪与无模型调用预检证据，不代表 5 实例链路检查或 20 实例正式批已完成；
实际运行及官方评分结果另行记录。
