# 人机协作控制面

控制面通过 `AntiShortcutSkill.control` 组织 Diff、任务看板、Goal、权限和本地维护任务；
文件写入、命令执行和阶段推进仍经过原来的拦截器、校验器和五道防线。
默认工作区保持现有工具行为；创建权限策略后，未认证会话一律拒绝。
未初始化时不缓存写前 Diff，任务写操作和 Goal 管理也不可用；状态查询仍兼容旧工作区。
Task Board 不会推进阶段，Goal 只能在未开始的初始状态或干净交付后绑定新循环。

## 启动与身份

```bash
pip install 'phase-barrier[tui]'
anti-shortcut permissions-init --workspace .
```

`permissions-init` 是可信本地初始化命令，输出一次人工 admin 令牌；策略存在后不能重新初始化。
将令牌放入 `PHASE_BARRIER_SESSION_TOKEN`，不要提交到仓库，也不要交给 Agent。
CLI 从这个环境变量取令牌，HTTP 使用 `Authorization: Bearer <token>`，gRPC 使用
`authorization` metadata。服务端只存令牌的 SHA-256；令牌绑定角色、会话 ID、
actor（`human` / `agent`）、过期时间，客户端不能自行声明角色。

```bash
anti-shortcut control session_issue --data '{"role":"worker","actor":"agent"}'
anti-shortcut control session_issue --data '{"role":"observer","actor":"human"}'
anti-shortcut sidecar --workspace . --host 127.0.0.1 --port 8080
anti-shortcut tui --url http://127.0.0.1:8080
```

命令示例使用 POSIX shell 的 JSON 引号；PowerShell 可用单引号包围同样的 JSON。
新令牌默认 24 小时有效，`ttl` 可指定 1–604800 秒。
撤销会话：`control session_revoke --data '{"session_id":"..."}'`。
`admin` 仅允许人工身份；发令牌、改策略和紧急授权都要求人工 admin。

| 角色 | 能力 |
|---|---|
| observer | 查询状态、证据、审计、diff、任务与 Goal |
| worker | 上述 + 受阶段约束的写入/执行/推进、捕获/认领/完成任务 |
| maintainer | 上述 + 任务排队/丢弃、Goal 管理；人工身份可复核和配置调度 |
| admin | 上述 + 人工会话管理、权限策略管理、一次性授权 |

进程内使用 `AntiShortcutSkill(..., session_token=token)` 或
`PhaseBarrier(..., session_token=token)`；HTTP 客户端使用 `GateClient(url, token=token)`。
在进程内拥有 Python 对象和文件系统权限的代码仍属于可信代码；部署隔离边界与
[sidecar 部署](k8s.md)一致，Agent 不应拥有门禁目录、服务端令牌或进程访问权。

## 权限策略与人工 Diff 复核

`.agent_gate/permissions.yaml` 示例（经 `control policy_update` 的 `policy` 参数提交）：

```yaml
version: 1
roles:
  observer: [read_state, read_evidence, read_diff, read_audit, read_tasks, read_goals]
write_paths:
  worker: ['spec.md', 'src/*', 'tests/*']
review_writes: ['spec.md', 'src/*']
rate_limits:
  write_file: 60
```

省略角色采用内置权限；显式角色列表只能缩小权限；空列表拒绝该角色全部操作。
路径使用工作区相对 POSIX 路径和 `fnmatch` 模式，`*` 可跨目录。
空白、绝对路径、反斜杠、盘符、`.`/`..` 路径段与未闭合字符类均视为配置错误，
更新时拒绝；已落盘的非法策略按损坏策略处理。合法但拼错的范围只会不匹配，不会回退放行。
策略损坏或初始化标记存在而策略缺失时拒绝访问。
速率限制按会话/操作在 SQLite 中计数，窗口为 60 秒。

`write_file` 在阶段检查通过后、落盘前计算 unified diff，缓存到
`.agent_gate/diff/latest.json`，复核队列持久化在 `tasks.db`。
文本差异含新增文件、原内容与目标内容哈希、阶段、会话、Goal 和里程碑上下文。
二进制文件显示摘要；超过 2 MiB 的文件仅缓存哈希和提示，不生成完整可视化。
需要复核的写入返回拒绝，原文件保持原状；人工决定后由调用者重试原写入。

```bash
anti-shortcut control diff_decide --data '{"review_id":"...","approved":true,"reason":"已核对接口"}'
```

批准仅用于对应会话、内容、旧文件、阶段循环，成功写入后消费。
内容修改、其他会话、原文件变化或阶段变化都要求重新复核。
驳回的内容须修改后重新提交。需要复核的目标不能通过已识别的 shell 写操作修改。
所有门禁目录写入在插件规则之前被拒绝。

紧急授权采用**受限的权限委托**：人工 admin 可调用 `break_glass_issue`，参数为
`path`、`session_id`、`reason`、`ttl`（最多 900 秒）；目标会话以
`GateClient.write_file(..., break_glass=token)` 使用。令牌只允许一次、一个文件、一个会话，
仍须通过阶段、目录与人工 Diff 复核；不会跳过证据，也不允许写门禁内部文件。
令牌在尝试写入前消费，后续失败也不会恢复；颁发与消费都有 `permission.break_glass*` 审计。

## TUI / Web

TUI 显示阶段 0–6、防线状态、任务看板、Goal 进度、Diff 和统一事件流。
按 `s` 切换 unified/split，`q` 退出。它通过 sidecar API 获取快照，增量订阅
`GET /api/events?cursor=<byte-offset>`，不会直接打开工作区状态文件。
事件包包含 `cursor`、`type`、`timestamp`、`data`，包括 `tool.start`、`tool.complete`、
`tool.blocked`、`stage.changed`、`write.preview`、`human_input_required` 和控制面审计事件。

唯二可写的 UI 动作是人工复核批准/驳回（diff 或交付复核）和 btw 任务认领。
操作由服务端再次鉴权。输入对应复核 ID 和理由，或队列任务 ID 后点击操作即可；
复核通过不会自动写入或推进阶段。Goal 创建和权限管理使用 CLI/API。
交付复核复用现有审批文件，驳回会撤销同 request ID 的旧批准。
Goal 不同里程碑的交付复核 ID 相互隔离。

## 安全承诺与已知残余风险

完整案例及复现命令见[控制面安全验证](control-security.md)。

| 类别 | 承诺与边界 |
|---|---|
| 机制保证 | 7 个 break-glass 红队案例验证文件/会话绑定、过期、复用、落盘失败后消费、目录与阶段约束；授权不授予推进阶段能力。 |
| 机制保证 | SQLite 事务强制任务跳转与唯一认领；btw 完成须显式回主流程；stale 只加标记。 |
| 机制保证 | Goal 的最后里程碑也先聚合状态和清单证据再标记完成；abandon 保留原状态及文件。 |
| 机制保证 | 已启用权限的 API 在服务端检查身份、角色、人工身份、到期与撤销；直接请求不降低要求。 |
| 机制保证 | TUI 仅有两类写动作：复核决定与 btw 认领；三个按钮为批准、驳回、认领，无直接阶段修改入口。 |
| 依赖人工 | DoD 只校验为非空文本列表；自然语言是否客观、需求是否实现、复核是否充分，需人评估。 |
| 已知残余风险 | `textual serve` 使用进程环境中的单一令牌，所有浏览器连接代表同一身份。远程部署必须另设身份认证、TLS 和访问隔离，不可直接公开管理员进程。 |
| 已知残余风险 | 拥有服务端 Python 对象、门禁目录写权限或进程访问权的代码属于可信边界内；本库不提供操作系统沙箱。shell 路径识别仍为启发式，write_audit 仅检测。 |
| 已知残余风险 | 调度租约依赖本机时间；崩溃或租约过期后可能重试，不承诺 exactly-once，不会自动推进阶段。 |

一次性授权在策略、身份及目标绑定检查通过后先消费，再做阶段、Goal、Diff 检查与落盘。
匹配令牌在这些后续检查或写入失败后不可恢复；错误路径/其他会话不会窃取消费令牌。
传参非法或目标不是文件时，代理可在进入授权路径前拒绝。控制面红队当前无逃逸豁免；
上述部署和观测限制不等同于允许测试逃逸。

Web 复用同一应用，无独立前端：

```bash
textual serve "python -m anti_shortcut.control.tui"
```

通过 `PHASE_BARRIER_URL` 和 `PHASE_BARRIER_SESSION_TOKEN` 配置服务端地址和会话。
Web 进程令牌代表全部浏览器连接，因此远程部署时应由外层认证保护该 Web 实例，
按身份分开部署。HTTP API 支持沿用 sidecar mTLS。

`textual-diff-view` 当前要求 Python ≥3.11（并使用 AGPL-3.0 许可），只作为 TUI
可选依赖安装；核心包不导入它。Python 3.10 使用 Rich/Pygments unified diff 回退，
无 split 视图。Textual 和 diff 插件均不成为核心强制依赖。

## Task Board

```bash
anti-shortcut task capture --type btw --title '顺便修正文档 typo'
anti-shortcut task list
anti-shortcut task queue --task-id <id>       # maintainer/admin
anti-shortcut task claim --task-id <id>       # worker 或人工认领
anti-shortcut task complete --task-id <id> --return-to-main
```

SQLite 强制 `captured → queued → active → done`，未完成任务也可由有权限的认领者丢弃。
并发认领只允许一个会话成功；人工认领记录 `human_owned`，其他会话不能完成或丢弃。
btw 完成必须显式 `--return-to-main`，记录审计。`stale` 是独立标记，避免破坏原状态机；
只标记长期无人处理的 captured/queued btw，不自动完成、丢弃或抢占活跃任务。
所有状态转换与阶段状态机独立，认领 btw 不赋予额外文件写入或阶段权限。

## Goal 多阶段循环

```bash
anti-shortcut goal create --title '重构模块' --done '回归测试全部通过' \
  --milestone '重构接口' --milestone '迁移调用方'
anti-shortcut goal status
anti-shortcut goal pause --goal-id <id>
anti-shortcut goal resume --goal-id <id>
anti-shortcut goal next --goal-id <id>
```

DoD 和里程碑必须是非空文本列表；调用者负责提供客观可判定的条件。
每个里程碑从阶段 0 开始；必须经过完整校验、交付测试新鲜且全绿、证据清单完整后，
才允许 `next`。最后一次 `next` 聚合最后里程碑证据并完成 Goal。
每次循环在签名状态里保留旧状态，在 Goal 中聚合 manifest 与阶段证据，
新循环清除上次测试结果。完成状态表示阶段证据通过，DoD 的自然语言含义仍需人评估。

暂停阻止写入、命令执行和阶段推进；恢复不重置当前循环，可跨会话继续。
绑定循环时先持久化 Goal 意图，进程中断后用 `resume` 恢复未完成的绑定。
同一工作区只允许一个未结束 Goal，不能创建 Goal 覆盖阶段 2–5 的现有工作。
`abandon` 留存原阶段证据并停止该 Goal，不提供丢弃未交付证据的状态重置捷径。

## Sidecar 定时维护

人工 maintainer/admin 通过 `control schedule_update --data '{"config": ...}'` 设置
`.agent_gate/schedule.yaml`，然后重启 sidecar。调度器仅随 sidecar 主进程启动，
Agent API 没有运行、启动、停止调度器的方法。

```yaml
version: 1
jobs:
  evidence_integrity_check: {interval_seconds: 3600}
  residual_expiry_check: {interval_seconds: 86400}
  btw_stale_cleanup: {interval_seconds: 86400, stale_days: 30}
  goal_health_report: {interval_seconds: 86400}
  red_team_fast: {interval_seconds: 604800, enabled: false}
```

采用标准库线程循环、持久化下次运行时间与 SQLite 执行租约；重启不重置已持久化周期，
失败写审计并等待下次周期重试。各任务不会修改阶段状态：

| 任务 | 行为 |
|---|---|
| evidence_integrity_check | 重载并验证状态签名、证据清单与文件哈希 |
| residual_expiry_check | 检查当前工作区 `benchmarks/red_team/known_residual.yaml` 过期项 |
| btw_stale_cleanup | 标记过期且未认领的 btw |
| goal_health_report | 输出 `.agent_gate/reports/goal-health.json` |
| red_team_fast | 在可信源码安装树运行固定的 equivalent_op/timing_evade 子集，逃逸返回失败 |

红队任务需要安装来源包含完整源码 checkout；普通 wheel 中不存在 benchmarks 时明确失败，
不会运行工作区里同名脚本。所有任务结果写入 `scheduler.completed`，TUI 可见。
本地维护只处理当前工作区，GitHub Actions 继续负责跨 PR/仓库 CI。

## API

`GET /api/control` 返回快照。`POST /api/control` 使用
`{"operation":"task_capture","data":{"title":"..."}}` 调用显式操作白名单。
可用操作包括 `task_capture`、`task_transition`、`goal_create`、`goal_action`、
`diff_decide`、`review_decide`、`session_issue`、`session_revoke`、`policy_update`、
`break_glass_issue`、`schedule_update`。初始化与调度器生命周期不在白名单中。
相同 API 可由 `GateClient.control(operation, **data)` 与 `control` CLI 调用。
所有控制面成功写操作写入既有审计日志；权限拒绝与阶段拒绝分别保留原因。
