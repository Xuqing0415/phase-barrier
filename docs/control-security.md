# 控制面安全验证与发布门槛

控制面沿用阶段门禁的对抗标准，信任前提是服务端进程、门禁目录与客户端工作区隔离。
每次增加写能力，先验证是否引入跳过阶段的路径。

## 可重复的对抗验证

`benchmarks/control_red_team/` 独立管理案例，复用现有框架的结果与过期豁免判定，
也能通过旧入口的 `--techniques` 选择。五类共 36 个案例：

| 技术 | 数量 | 验证边界 |
|---|---:|---|
| `break_glass_reuse` | 7 | 其他文件/会话、过期、复用、失败仍消费、门禁目录、阶段 |
| `task_state_skip` | 6 | 跳步、并发唯一认领、人工所有权、回主流程、stale |
| `goal_binding_escape` | 10 | 阶段 2–5 绑定、未完成 next、最后证据、abandon、第二目标、暂停、篡改 |
| `control_bypass` | 6 | 无令牌、过期、撤销、observer 复核、worker 建 Goal、Agent 复核；真实 HTTP |
| `policy_fail_open` | 7 | 策略损坏/缺失、空角色、非法/拼错范围、限流、授权下策略损坏 |

```bash
python -m pip install -e '.[dev,tui]'
python -m pytest tests/test_control_plane.py tests/test_control_security.py tests/test_control_security_gates.py -q
python benchmarks/control_red_team/run_control_red_team.py --workspace .pytest_tmp/control-rt --output .pytest_tmp/control-report --json
```

Goal 完整循环运行真实 pytest 子进程，通过公开接口提交真实输出，没有伪造绿测记录。
故障注入只用于到期、持久化中断、写盘失败等边界。断言不成立报告 `escaped`，
运行异常报告 `inconclusive`，二者均使控制面运行失败；不会将故障冒充拦截。
本地没有 LLM/TLC 验证，不能据此宣称实时语义或模型检查通过。

独立入口默认账本 `benchmarks/control_red_team/known_residual.yaml` 当前为 `entries: []`。
逃逸默认非零；豁免须登记 `technique`、`case_id`、`reason`、`expires_at`，
配置损坏或任何豁免过期均失败。案例自身的 expectation 不能让 CI 变绿。

## 覆盖率与 CI

测量行与分支之和，保留原始百分比，不用四舍五入跨越门槛：

```bash
python -m coverage run --branch -m pytest tests/ -q -s
python -m coverage json -o .pytest_tmp/control-coverage.json
python scripts/check_control_coverage.py .pytest_tmp/control-coverage.json
```

Windows 控制台可设 `PYTHONIOENCODING=utf-8`。`-s` 避免 pytest 的终端捕获干扰
Textual 无头交互；CI 安装 `dev,tui` 依赖，缺少可选界面依赖的跳过不算界面验证完成。

- 核心包至少 90%；control 至少 90% 且不得低于同一报告的核心包覆盖率。
- `permissions.py` 与包含 Task/Goal 状态机的 `service.py` 各至少 95%。
- 缺分支数据、核心包数据或关键文件数据直接失败。
- red-team 工作流含 control-security job 和快速技术子集，覆盖 main push、相关 PR、
  每周定时和手动执行，无 `|| true` 放行路径。

## 本轮核对与发布状态

2026-10-20 远程最高正式标签为 v0.51.0，尚无 v1.0.0 / v1.1.0。
CHANGELOG 的 1.0 条目是 2026-09-12 写入的里程碑说明，不是发布证明。
README 已区分实际标签和计划版本，不能把尚未生成的标签写成已发布。

本轮复现非法路径模式被接受，已增加更新校验和落盘非法策略拒绝测试；
紧急授权的策略检查前移到消费之前。原实现已有后续策略拒绝，未据此宣称可绕过落盘。
未初始化时不再生成 Diff/任务写产物，保证显式启用；界面渲染加锁防止 widget 操作交错。

发布依次要求安全测试、同版本覆盖率、严格文档构建和远程 CI 全绿。
先为可核对的 1.0 里程碑源码建立真实标签，再发布包含控制面的 v1.1.0；
不得把控制面混入假装历史发布的 v1.0.0。标签会触发 PyPI 发布，前置条件不满足时保持待发布。

## 观察与扩样

write_audit 保持检测模式。本地回归验证 Diff 缓存、SQLite 和 Goal 报告被门禁目录
排除规则排除，不误记为 Agent 写入；这不能代替未来 2–4 周真实使用观测。
保留未经授权写事件的路径、来源、阶段与误报归因，不自动放宽策略。

Scale-50 固定抽样清单、模型、预算及双组评分口径；未得到 50 个完成配对前，
不得复用 Scale-20 成功率作为新结果。技术博客待安全门槛完成后再写。
