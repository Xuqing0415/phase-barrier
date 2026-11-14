# 从 Verified20 复核到 strict 扩样：执行记录

用户方案接收于 2026-11-14。按阶段验收，后续阶段不越过前一阶段退出条件。

| 阶段 | 状态 | 验收 |
|---|---|---|
| 一：异常复核 | 已完成 | 40/40 证据重新核验；三 SymPy suspicious，命令漏识别而非日志漏写；后期未完成 7、早期实现校验未完成 1；SymPy 双组跨睡眠 |
| 二：最终报告 | 已完成 | 提交 dff58c9 已推送 main；CI、docs、Security、Plugin CI 均 success；56 项相关测试及 MkDocs strict 通过 |
| 三：测试演进约束 | 最终源码回归与冒烟验收完成，待远端 CI / 标签 | warn/strict/off、删除理由审计、真实 Xarray fixture、Java/Go 名字及复合命令修复；1808 passed / 22 skipped，核心覆盖率 93.68%；两轮三实例均门禁完成 3/3、官方 resolved 2/3 |
| 四：100 个新增实例扩样 | 待阶段三验收 | 排除历史 49 个不同实例，预注册 100 个新实例、200 个任务，strict、镜像/DNS/监督前置检查、完整配对统计 |

## 复核导致的方案修正

- 三个正式 SymPy 不能归 benign：阶段 2 的 `cd ... && python -m pytest ...` 被漏识别，原工具日志与 trace 相符。阶段三同时修复此已确认缺口。
- scikit-learn-25102、Sphinx-9281 分别停在 5/4，归后期组；Pylint 停在阶段 3，测试已完成，被存量负向语法夹具阻断。
- 系统事件确认 SymPy-18211 双组跨 2026-11-13 11:59:40–14:22:42 宿主机睡眠。主要时间样本由 17/18 修正为 16/17；原结果不改写。
- 重算后独立部分 baseline/gated P90 为 641.8/767.1 秒，描述性比值 **1.20×**；1.35× 为发现睡眠前的旧口径。
- 四个异常类别合计 15 次归属、14 个不同任务，加另列的 Matplotlib baseline 后异常表为 15 行。
- “49 个”是历史不同实例数，不保证观测独立。扩样不按显著性筛选或停止；样本量不能保证方向或 p<0.05。
- 测试名保留只防止未声明删除/重命名，不保证同名断言强度、语义或覆盖率不下降；CHANGELOG 需准确表述。
- 仓库实际生成器使用 `--update-main --output ... --summary ...`，没有 `--finalize`；运行器也不接受整数 `--tasks 3` 或 `--mode`，须用清单和 `--modes gated`。

## 当前环境

Docker Desktop Linux engine 已恢复（服务端 29.6.1）；三实例镜像 3/3 就绪，模型凭据仅检查存在性，未输出或写入报告。
2026-11-14 已独立确认 dff58c9 的四项远程工作流全部 success。
Docker 恢复：Windows 原生文件移动/重命名无法处理旧重解析点；在 Docker 停止时通过 WSL 无覆盖备份 `docker-secrets-engine/engine.sock`，再备份仅含旧 `dockerInference` 的 `Docker/run` 目录并重建，启动成功。未清理凭据或重置 Docker；保留全部备份（含先前留下的空目录）。

## 阶段三本地验收（2026-11-14）

- 最终源码 `d079f69` 的全量 `coverage run -m pytest tests/`：1830 项收集，1808 passed、22 skipped、0 failed；核心行+分支覆盖率 93.6781%。前一轮为 1807 passed / 22 skipped；补齐空登记跳过的边界用例后全量重跑。
- 同一覆盖率报告的 control 97.0332%、permissions 99.1489%、service 98.7915%，`check_control_coverage.py` passed。
- 新增测试保留专项 29 项，另增复合命令/运行器接线测试；Xarray 两份真实 `write_file` 内容连同 SHA256 来源记录入 fixture。
- 红队使用工作流定义的十项快速技术：54 案例中 53 blocked、1 inconclusive，无未豁免逃逸、无过期豁免。首次运行仅输出阶段遇到 GBK 编码错误，使用 UTF-8 与新工作目录重跑成功。
- MkDocs strict 与 `git diff --check` 通过。摘要见 `benchmarks/swebench/results/v120-validation.json`；原始本地证据位于 `.pytest_tmp/stage3-*`。
- 三实例 strict 冒烟固定为 Xarray-3993、SymPy-13480、Flask-5014；60 轮、并发 2、显式 DNS。首轮目录 `.pytest_tmp/v120_smoke_20261114`，修正空登记边界后按原清单全部重跑于 `.pytest_tmp/v120_smoke_final_20261114`，未按结果替换实例。
- 最终源码哈希与启动快照一致；三个 trace / stats / state / patch / 官方 report / prediction 核验通过，门禁均完成到阶段 6，登记测试名均保留。三个实例都实际拦截了阶段 1/2 的复合 pytest 命令；测试保留专门拒绝数为 0（没有删除登记名），拒绝场景由真实 Xarray fixture 覆盖。
- 两轮官方结果均为 SymPy / Flask resolved，Xarray unresolved（2/3），不能写为官方 3/3 通过。Xarray 仍是 Agent 实现及自测使用 DeprecationWarning、官方要求 FutureWarning 的契约差异；测试名保留不验证该语义。冒烟验收只确认门禁流程和证据链，已单独审阅此已知局限，不据此宣称门禁完成蕴含官方解决。
- 机器可读最终证据见 `benchmarks/swebench/results/v120-smoke.json` 与 `v120-validation.json`。发布候选的远程 CI 与最终源码 `d079f69` 的手动红队（43564774497）需全部通过后才打 v1.2.0 标签；阶段四尚未启动。
