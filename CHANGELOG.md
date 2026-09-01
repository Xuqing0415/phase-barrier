# Changelog

版本号由 git tag 驱动（`setuptools-scm`）：打 `vX.Y.Z` tag 后构建的发行包即为 `X.Y.Z`。
## [0.26.2] - 2026-09-01

- 编排器 SDK 辅助查询（v0.26.2）：
  - `PhaseBarrier.list_stages()`：返回阶段清单（编号 / 名称 / 准入门槛 / 必需证据，
    JSON 可序列化），元数据集中定义于 `config.STAGE_META`。
  - `PhaseBarrier.stage_of(path)`：把文件路径归类到对应阶段的证据
    （spec→1 / test→2 / source→3 / other→None），与 `verify-evidence --git-base`
    的 `git_impact` 分类一致。
  - `verify-evidence --git-base` 的变更影响映射重构为复用 `sdk.classify_stage_path`
    （输出字段不变，编排器与 CLI 分类口径统一）。
- 生态：`docs/plugins.md` 收录第一批官方示例插件索引
  （`phase-barrier-foo-adapter` / `phase-barrier-plugin-example` / 编排器钩子集成），
  社区第三方插件提交流程保持开放。
- 测试：新增 7 个 SDK 查询用例（全量 620 → 627）。

## [0.26.1] - 2026-09-01

- CI 修复：coverage 门禁 89% → 90%（新增 10 个边界用例：shlex ValueError / 反斜杠续行、
  规则弃权分支（无 config / 非 write / 非源码扩展 / 空内容）、C++ 输出解析兜底分支、
  `_decode_output` 回退、空文件与编译器缺失的语法检查；全量 605 → 620）。
- release 工作流：docker action 升级到支持 Node 24 的大版本
  （`setup-buildx-action@v4` / `login-action@v4` / `build-push-action@v7`），消除 Node 20 弃用告警。

## [0.26.0] - 2026-09-01

- 产品化与生态建设（v0.26.0）：
  - 配置脚手架：新增 `python -m anti_shortcut init`，自动检测语言生成带注释的 YAML 模板
    （可选 `--with-coverage` / `--coverage-threshold` / `--hmac-key` / `--audit-url` / `--rules`）；
    新增全字段配置指南 `docs/configuration.md`（由 `GateConfig` 自动枚举）。
  - Docker 一键体验：新增 `docker/demo/`（Dockerfile + 模拟 Agent `agent_demo.py`），
    release 工作流自动构建并推送 `ghcr.io/xuqing0415/phase-barrier-demo`（tag + latest）。
  - C++ / .NET 适配器：`CppAdapter`（g++/clang++ `-fsyntax-only`、GoogleTest 宏统计、
    `ctest` / GoogleTest 输出解析）与 `DotNetAdapter`（复用 C# 项目级 `dotnet build` 与 VSTest
    输出解析）；自动检测 `CMakeLists.txt` / `Makefile` / `*.vcxproj`，可显式 `language: cpp|dotnet`。
  - PR 增量校验：`verify-evidence --git-base <ref>` 新增 `git_impact` 变更影响映射
    （spec / test / source / other）；GitHub Action 新增 `mode: verify` 与 `git_base` 输入，
    示例 `examples/github-action/gate-pr.yml`；CI gate-action 自测扩展正反用例。
  - 内置安全规则包：新增 `anti_shortcut/rules/`，含 `no_shell_injection` / `no_path_traversal` /
    `no_hardcoded_secrets` / `require_license_header` 四条规则，YAML `rules:` 一键启用，
    `write_file` 内容透传参与规则校验。
  - 生态：插件索引 `docs/plugins.md`、贡献指南 `CONTRIBUTING.md`、Issue 模板
    （bug_report / feature_request / plugin_submission）。
  - 测试：新增 init / 内置规则 / C++ / .NET / Action verify / git_impact 用例，全量约 600。

## [0.25.1] - 2026-09-01

- GitHub Action 修复：composite action 的 `outputs` 必须通过
  `value: ${{ steps.<id>.outputs.<name> }}` 映射才能传播到调用方，仅写 `$GITHUB_OUTPUT`
  不会生效（v0.25.0 的 gate-action 自测 "Assert inspect outputs" 因此读到空值）。
  - `action.yml` 三个输出（`workspace` / `stage` / `allowed`）补上 `value` 映射。
  - action 内部 `actions/setup-python@v5` 升级到 `@v7`，消除 Node 20 弃用告警。
  - `tests/test_action_meta.py` 增加 outputs value 映射断言（共 11 个用例）。

## [0.25.0] - 2026-09-01

- GitHub Action 市场元数据增强（v0.25.0）：
  - `action.yml` 新增 `outputs` 声明（`workspace` / `stage` / `allowed`），门禁步骤 `id: gate`
    在成功路径输出阶段号与放行结果，失败时 `allowed=false`，下游步骤可通过 `steps.gate.outputs.*` 复用。
  - 输入参数联动说明补齐：`advance` 需配 `to`，`check` 需配 `stage`，`exec` 需配 `command`。
  - CI / Release 工作流升级 checkout@v7、setup-python@v7、setup-node@v7、setup-go@v7、
    upload-artifact@v7、action-gh-release@v3（消除 Node 20 弃用告警）；gate-action 自测新增 outputs 断言。
  - README 增加 Marketplace 徽章、Action 输出用法示例，示例版本更新至 `@v0.25.0`。
  - 新增 `docs/publish-to-marketplace.md`（in-tree 上架流程与检查清单）与 `tests/test_action_meta.py`
    （action.yml 元数据测试，新增 10 个，全量 538 → 548）。

## [0.24.0] - 2026-09-01

- Java 适配器输出解析剩余项（v0.24.0）：
  - Surefire 参数化用例：`methodName[displayName](Class)`（displayName 含逗号 / `[N]` 序号），兼容 `[ERROR]` 前缀；
  - `<<< ERROR!` 超时 / 异常细分：异常块含 `TimeoutException` / `TestTimedOutException` / `timed out`
    判定为「超时」，否则「异常」，失败用例摘要附类型标注；
  - Gradle：`Class > method SKIPPED` 行计数兜底、`BUILD SUCCESSFUL` 汇总、多模块 reactor 输出聚合
    （多个 `N tests completed, M failed` 行求和）；
  - JUnit Platform Console：`MethodSource` 嵌套格式 `Class.method(ParameterizedTest)[N]` 与失败条目 `[N]` 序号后缀；
  - 测试命令识别补充 Windows wrapper：`mvnw.cmd test` / `gradlew.bat test` / `.\mvnw test`；
  - 新增 10 个解析边界测试（`tests/test_java_adapter.py`），全量 528 → 538。


## [0.23.0] - 2026-08-30

- Java 适配器输出解析增强（v0.23.0）：
  - 失败用例提取：Surefire `methodName(Class) <<< FAILURE!/ERROR!`、Gradle `Class > method FAILED`、JUnit Console `MethodSource [methodName=...]`，去重后最多 50 个；
  - `parse_test_output` 失败分支附带失败用例摘要，Gradle 汇总新增 `skipped` 计数解析；
  - 新增 6 个解析边界测试（`tests/test_java_adapter.py`），全量 522 → 528。
- GitHub Action 元数据增强（v0.23.0）：
  - 新增 `mode: check`：只读校验是否放行进入 `--stage` 阶段（`anti_shortcut check`），拒绝时输出 `::error::` 与明细；
  - 新增 `stage` 输入（check 模式）与 `cwd` 输入（exec 模式工作目录）；
  - 参数校验扩展 `check` 模式；CI `gate-action` 自测新增 check 放行 / 拒绝 / 缺参 3 组步骤。
- 文档：README 特性列表、GitHub Action 章节、Roadmap 同步更新。

## [0.22.0] - 2026-08-30

- 编排器钩子 SDK（v0.22.0，alpha-swe 集成面）：
  - 新增 `PhaseBarrier` 轻量 SDK（`anti_shortcut/sdk.py`）：任务启动 / 阶段切换钩子调用，
    传入项目目录与 Agent 声称的阶段，返回“是否放行 + 约束提示”（全部 JSON 可序列化 dict）；
  - `check(stage)`：只读校验前置证据，支持放行 / 拦截 / 跳步检测 / 非法参数结构化返回；
  - `advance(to_stage)`：与 `advance_stage` 同一套证据校验，返回增加稳定 `stage_name` 字段；
  - `record_test_run(result)`：编排器登记测试运行结果，供阶段 4 推进校验；
  - `verify_evidence()`：证据清单校验，清单缺失 / 签名不匹配统一捕为 `ok=False`；
  - `PhaseBarrier()` 无参调用默认当前工作目录，`AntiShortcutSkill` 全部行为向后兼容。
- CLI 新增 `check` 子命令：`python -m anti_shortcut check --stage N [--json]`（0 = 放行，1 = 拒绝）。
- 编排器集成示例：`examples/orchestrator_hooks/`（demo.py + README，任务启动 / 阶段切换两钩子全流程演示）。
- README 新增“编排器集成”章节并交叉引用 alpha-swe；CI demo 步骤同时运行编排器示例。
- 测试：新增 28 个 SDK / CLI 用例（`tests/test_sdk.py`），全量 494 → 522。

## [0.21.0] - 2026-08-30

- 审计查询增强（v0.21.0）：
  - `GET /api/audit` 新增 `offset` 分页与 `since` / `until` 时间范围过滤（ISO 时间戳，含端点），响应增加 `total` / `offset` 分页元信息。
  - `GateClient.audit(limit=..., offset=..., since=..., until=..., event=...)` 同步支持。
- 远程证据校验：
  - `GET /api/verify-evidence` 返回 `{ok, violations, entries, signed}`；`GateClient.verify_evidence()` 客户端方法。
- sidecar 入站 mTLS 访问控制：
  - `python -m anti_shortcut sidecar --tls-cert ... --tls-key ... --tls-client-ca ...`（`CERT_REQUIRED`，未携带客户端证书在 TLS 握手即被拒绝）。
  - `GateClient(base_url, cert=(crt, key), ca=...)` 支持客户端证书；示例 `examples/mtls_sidecar/`（generate_certs.py + demo.py + README）。
- 测试：新增 10 个用例（`tests/test_sidecar_v21.py`），全量 484 → 494。
- 文档：README 特性 / CLI / Roadmap、deploy/k8s 安全说明同步更新。

## [0.20.0] - 2026-08-30

- sidecar 审计查询 API（v0.20.0）：
  - `GET /api/audit?limit=50&event=proxy_write_denied` 读取本地 `audit.log`，按时间倒序返回最近事件，支持数量上限（1-500）与事件名精确过滤。
  - `GateClient.audit(limit=..., event=...)` 客户端方法；配合 v0.19.0 的 5 类代理审计事件，可远程核对「拦截是否发生、原因是什么」。
- CLI `sidecar` 子命令：
  - `python -m anti_shortcut sidecar --workspace . --host 0.0.0.0 --port 8080` 以统一 CLI 启动门禁 HTTP 服务（等价 `python -m anti_shortcut.sidecar`），K8s 清单与文档切换为新入口。
- 端到端审计链测试：HTTP 写拒绝 → 本地 audit.log → `/api/audit` 查询 → 远端 SIEM 推送，全链路验证。
- 测试：新增 11 个审计查询 / 端到端用例（`tests/test_sidecar_audit_api.py`），全量 473 → 484。
- 文档：README 特性 / CLI / Roadmap、deploy/k8s 清单同步更新。

## [0.19.0] - 2026-08-30

- 透明代理审计事件（v0.19.0）：
  - `write_file` 成功 / 被拒分别记录 `proxy_write_ok` / `proxy_write_denied`；`execute_command` 成功 / 被拒 / 超时分别记录 `proxy_exec_ok` / `proxy_exec_denied` / `proxy_exec_timeout`。
  - 每条事件携带阶段摘要（current_stage / stage_name / completed_stages），同时写入本地 `audit.log` 并推送远端 SIEM；原 `proxy_file_written` 更名为 `proxy_write_ok`。
- 命令工作目录（cwd）三端支持：
  - CLI `exec --cwd <dir>`；sidecar `/api/exec` 新增可选 `cwd` 字段；`GateClient.execute_command(command, cwd=...)`；路径须解析在工作区内，越界返回 400 / 拒绝执行。
- 测试：新增 12 个代理审计与 cwd 用例（`tests/test_proxy_audit.py`），全量 461 → 473。
- 文档：README 特性 / CLI / Roadmap 同步更新。

## [0.18.0] - 2026-08-30

- CLI 透明代理命令（v0.17.0 HTTP sidecar 的命令行形态）：
  - `python -m anti_shortcut write --workspace . --path <file> --content/--stdin`：
    经门禁写入工作区文件（路径限定工作区内、拒绝 `.agent_gate`、按阶段拦截），
    被拒绝退出码 2；`--json` 输出 `{ok, path, kind}`。
  - `python -m anti_shortcut exec --workspace . --command <cmd> [--timeout N]`：
    经门禁执行 shell 命令，测试命令自动解析输出并写入状态机；
    放行后退出码 = 命令自身退出码，被拒绝退出码 2；`--json` 输出
    `{ok, exit_code, output, recorded_test_run}`。
- GitHub Action 门禁新增 `mode: exec` 与 `command` 输入：
  - 在 CI 中经门禁执行测试/校验命令，命令失败或门禁拒绝都会让步骤失败（set -e）；
  - `mode` 校验扩展为 inspect / advance / exec；CI `gate-action` 自测新增
    exec 通过 / 命令失败 / 缺 command 三条路径。
- 测试：新增 16 个 CLI 门禁用例（write 拦截/放行/stdin/参数冲突/路径越界、
  exec 拦截/输出/非零退出码/JSON/超时/测试结果记录），全量测试 445 → 461。
- 文档：README 特性 / Action 输入表 / CLI 章节 / 模块结构 / Roadmap 同步更新。


## [0.17.0] - 2026-08-30

- K8s sidecar 透明代理（阶段门禁下沉到文件系统层）：
  - sidecar 新增 `POST /api/write`（`{"path", "content"}`）与 `POST /api/exec`
    （`{"command", "timeout"}`）：路径必须解析在工作区内（拒绝 `../` 越界与绝对路径逃逸）、
    拒绝 `.agent_gate`、按阶段拦截 test / source / other 写入与测试命令
    （与 `AntiShortcutSkill` 工具包装器同策略）；exec 通过后在共享工作区执行，
    若是测试命令自动解析输出并写入状态机（无需再单独调 `/api/test-run`）。
  - 超时处理：`subprocess` 超时后尽力终止整个进程树并立即返回，不等待孤儿进程释放管道
    （Windows 下 `taskkill /T` 不可用时回退 `kill`，避免孙进程阻塞响应）。
  - 新增 Agent 侧客户端 `anti_shortcut.proxy_client.GateClient`（仅标准库 urllib），
    被拦截抛 `GateDenied`；新增最小示例 `examples/k8s_proxy/`（自包含 demo）。
  - `deploy/k8s/` 清单与文档更新：镜像版本 0.17.0、拓扑图与接入协议补充代理端点。
- 测试：新增 30 个透明代理用例（GateProxy 单元 / HTTP 端点 / GateClient 全流程），
  全量测试 415 → 445。
- 文档：README 特性 / 架构 / 模块结构 / Roadmap 同步更新。


## [0.16.0] - 2026-08-30

- CI 真实工具链激活：
  - test 矩阵与 coverage job 安装 Node.js 20 / Go 1.22 / Rust stable / Ruby 3.3，
    激活 JS / Go / Rust / Ruby 适配器的真实工具测试（此前按环境跳过，存在“假绿”盲区）。
  - 新增 3 个真实 node 用例：`node --check` 成功 / 失败、`js_count_tests.cjs` 无 acorn
    时回退启发式统计（CI 安装 node 后自动执行）。
- 输出解析与覆盖率门禁边界补强（新增 29 个用例）：
  - `summarize_test_output` / `_extract_coverage` 剥离 ANSI 颜色码，避免带色输出无法提取
    摘要与覆盖率；istanbul 表支持千分位（`1,234`）。
  - Java Surefire `Skipped: 0` / Gradle `N tests completed, M failed` / JUnit Console
    成功输出；Go 混合 `ok` / `--- FAIL:`；Rust `test result: ok` 无 `failures:` 块 /
    编译错误；Vitest / Playwright 摘要；空输出按退出码判定。
  - 覆盖率门禁：阈值恰好相等（通过）、略低于（拒绝并提示数值）、回归阶段缺失报告、
    非法配置值（`150` / `-1` 拒绝）——新增 `coverage_threshold` 0-100 字段校验。
  - sidecar CLI：`main()` 启停、`--state-key` 注入 `PHASE_BARRIER_HMAC_KEY`、
    `_merge_config` 命令行 / 环境变量 / 配置文件优先级。
  - Ruby / Rust 真实工具路径的 mock 确定性覆盖（不依赖本机工具链即可执行）。
- 覆盖率门禁（自举）：
  - `pyproject.toml` 新增 `[tool.coverage]`（source=`anti_shortcut`，branch=true，
    `fail_under=90`）；CI 新增 coverage job：`coverage run -m pytest` +
    `coverage report --fail-under=90`，上传 `coverage.json` 报告。
  - 当前核心包覆盖率 90%（含分支，本地 ruby/rust 真实用例跳过；CI 装齐工具链后更高）。
- 测试：383 → 415（+32：边界 29 + 真实 node 3）。
- 文档：README Roadmap / 特性 / CI 与覆盖率章节同步更新。

## [0.15.0] - 2026-08-30

- 审计远程推送故障告警（v0.15.0）：
  - `RemoteAuditSink` 新增 `on_failure(batch, retries)` 回调：重试耗尽时通知宿主，回调异常
    不影响门禁流程；新增 `metrics()` 暴露累计指标（enqueued / dropped / sent_events /
    sent_batches / failed_batches / spooled_events / recovered_events），供宿主监控与告警。
  - `AntiShortcutSkill` 自动接线：把 `audit_remote_failed` 告警事件写入本地 `audit.log`
    （专用本地 logger，不再转发已失败的远端，避免“告警 → 失败 → 再告警”的自喂循环）。
- sidecar HTTP 门禁输入校验：
  - `/api/advance` 拒绝 `bool`（Python 中为 `int` 子类）与越界阶段号（须为 0-6 的整数）；
  - `/api/test-run` 校验 `output` 必须为字符串；
  - `/api/source-change` 拒绝指向门禁目录 `.agent_gate` 的路径。
- 测试：新增 7 个用例（376 → 383）：`on_failure` 回调触发/异常吞掉/成功不触发、
  `metrics()` 键完整性、Skill 告警接线与本地落盘（含防自喂循环断言）、sidecar 输入校验。

## [0.14.0] - 2026-08-30

- 拦截器：脚本类写入检测（v0.14.0）：
  - `extract_written_paths` 新增脚本参数解析：识别 `python -c` / `node -e` / `perl -e` /
    `bash -c` 等代码内的 `open('x','w'/'a'/'r+')`、`Path('x').write_text / write_bytes`、
    `fs.writeFile(Sync)` / `appendFile(Sync)` / `createWriteStream` 与重定向写入路径；
    只读打开（`open('x','r')`）不误报，`=>` / `2>&1` 等操作符被排除；
  - 脚本写入同样走 `check_write_permission` 阶段门禁：未完成测试用例时
    `python -c "open('fib.py','w')"` 被拦截，进入实现阶段后放行。
- CLI：`verify-evidence` / `export-evidence` 错误处理补强：
  - 损坏清单（非法 JSON）、版本不兼容、配置 HMAC 密钥但清单未签名、缺失工作区
    均返回退出码 1 与明确中文报错，无堆栈；`--out` 指向不存在子目录时自动创建父目录。
- GitHub Action 门禁输入校验（`action.yml`）：
  - `mode` 必须是 `inspect` / `advance`；`expected_stage` 与 `advance` 的 `to`
    必须是 0-6 整数；`workspace` 必须存在；非法输入输出 `::error::` 并失败；
  - CI 自测扩展：`advance` 模式推进、非法 `mode` / `expected_stage` / 缺失工作区三条失败路径。
- 测试：新增脚本写入与 CLI 边界用例 17 个（359 → 376，另有 6 个按环境跳过）。
- 文档：README 特性 / GitHub Action 章节 / Roadmap 同步更新。

## [0.13.0] - 2026-08-30

- 拦截器边界与 CLI 错误处理补强：
  - `touches_gate_dir` 支持 `dd of=` / `--flag=value` 等 `=` 参数形式与引号包裹路径段
    （边界正则从 `(^|[/\\])` 放宽为 `(^|[^A-Za-z0-9_.-])`，仍排除 `foo.agent_gate/` 误报）；
  - `extract_written_paths` 新增 `&>` / `&>>` / `>|` 重定向运算符；
  - CLI：损坏状态（含 `--json`）、缺字段状态、越界阶段号（99 / 0 / 负数）、完成后推进、
    证据缺失 / 实现语法错误均给出明确退出码与报错信息。
- 插件文档与可运行示例：`examples/plugin_rules/` 提供自定义校验器 + 拦截规则完整插件包
  （`phase_barrier.validators` / `phase_barrier.interceptors` 入口点声明 + demo），
  支持进程内注册与入口点加载两种接入方式；README 补充插件章节、特性与 Roadmap。
- 测试：新增边界与 CLI 错误处理用例 16 个（343 → 359，另有 6 个按环境跳过）。

## [0.12.0] - 2026-08-30

- 插件机制（自定义校验器与拦截规则）：
  - `phase_barrier.validators` 入口点组 + 进程内 `register_validator(stage, fn)`：支持 `{stage: fn}` 映射 /
    带 `stage` 属性的单校验器 / 返回映射的工厂三种形式，覆盖内置阶段校验器；
  - `phase_barrier.interceptors` 入口点组 + 进程内 `register_rule(name, rule)`：规则签名
    `rule(kind, target, config, stage)`，返回 `(False, reason)` 拦截 / `(True, reason)` 放行 / `None` 弃权，
    在 `write` / `exec` 内置检查前评估，异常规则自动跳过；
  - `skill.py` 接线：`check_write_permission` / `check_exec_permission` 先评估自定义规则，
    `advance_stage` 改用 `get_validator` 解析校验器（自定义优先）。
- 审计 mTLS 端到端示例：`examples/mtls_audit/` 提供自签 CA + 服务端 / 客户端证书生成
  （含 SKI / AKI / KeyUsage 扩展）、mTLS 收集端点与一键 demo，验证
  `audit_remote_client_cert` / `audit_remote_client_key` 双向 TLS 链路。
- 发布：`pyproject.toml` 补 `ruby` / `csharp` 语言入口点与校验器 / 拦截规则入口点组占位；
  dev 依赖增加 `cryptography>=42`（仅示例与端到端测试需要）。
- 测试：新增插件校验器、拦截规则与 mTLS 端到端用例 21 个（322 → 343，另有 6 个按环境跳过）。
- 文档：README 增加“自定义校验器与拦截规则”章节，模块结构 / 特性 / Roadmap 同步更新，
  GitHub Action Marketplace 上架状态确认。
## [0.11.0] - 2026-08-30

- 审计远程推送增强（v0.11.0）：
  - mTLS 客户端证书：`audit_remote_client_cert` / `audit_remote_client_key`（PEM 成对配置，
    证书 / 私钥启动时即校验），支持双向 TLS 的 SIEM / webhook 端点。
  - 自定义请求头：`audit_remote_headers` 合并到每次 POST（token 仍以 `Authorization` 头优先）。
  - 持久化重试队列：`audit_remote_spool_dir` 把重试耗尽的事件落盘为 JSONL（`audit_spool.jsonl`），
    进程重启时自动恢复重新发送，避免崩溃 / 滚动重启丢事件。
  - sidecar 新增 `--audit-remote-client-cert` / `--audit-remote-client-key` /
    `--audit-remote-header NAME=VALUE`（可多次）/ `--audit-remote-spool-dir`，并支持对应环境变量。
- 证据清单 Git 门禁：
  - `verify-evidence --git-base <ref>` 用 `git diff --name-only <ref>...HEAD` 列出本次变更文件，
    与证据清单条目求交集——证据文件被本次提交修改即判定“事后篡改”并失败（JSON 输出含 `git_changed_files`）。
  - 新增 GitHub Action 示例 `examples/github-action/evidence-gate.yml`（checkout fetch-depth: 0 +
    `pip install phase-barrier` + `verify-evidence --git-base origin/main`）。
- 语言适配器：
  - 新增 `RubyAdapter`：`ruby -c` 语法检查 + RSpec（`describe` / `it` / `specify` + `expect`）与
    Minitest（`def test_*` + `assert_*`）启发式统计；`rspec` / `rake test` / `rails test` /
    `ruby -Itest` 命令识别；`N examples, M failures` / `N runs, M assertions` 输出解析。
  - 新增 `CSharpAdapter`：`dotnet build` 项目级语法检查（向上查找 `*.csproj` / `*.sln`，带指纹缓存）；
    `[Fact]` / `[Theory]` / `[Test]` / `[TestMethod]` 属性统计 + `Assert.*` 断言；`dotnet test` /
    `nunit3-console` / `dotnet vstest` 命令识别；`Passed! - Failed: F, Passed: P, ...` 输出解析。
  - 自动检测扩展：`Gemfile` / `Gemfile.lock` / `.ruby-version` / `Rakefile` / `*.gemspec` → `ruby`，
    `*.csproj` / `*.sln` → `csharp`（新增目录级 glob 标志支持）。
- 测试：新增审计传输 / spool、Git 门禁、Ruby / C# 适配器用例 46 个（276 → 322）。
- 文档：README 配置示例 / 安全特性 / 多语言支持 / Roadmap 同步更新。
## [0.10.0] - 2026-08-30

- 审计远程推送增强：
  - `RemoteAuditSink` 新增 `ca_bundle`（PEM 自定义 CA，`ssl.create_default_context` 构建 HTTPS opener，
    配置错误启动即报错）与 `retries` / `backoff_factor`（指数退避重试：`backoff * 2**attempt` 秒）。
  - 配置新增 `audit_remote_ca_bundle` / `audit_remote_retries` / `audit_remote_backoff_factor`。
- 证据清单导出：
  - 新增 CLI `export-evidence [--out <file>]`：导出清单 + 当前文件 SHA-256 / 大小 + 校验结果的可审计
    bundle（支持 HMAC 签名状态透传），供第三方审计或 CI 留档。
- 供应链签名（sigstore）：
  - release 工作流新增 `sigstore/gh-action-sigstore-python` 步骤，用 GitHub OIDC 身份对 sdist / wheel
    签名，`.sig` / `.bundle` 随 GitHub Release 发布；README 增加 `cosign verify-blob` 校验说明。
- 语言适配器输出解析增强：
  - `summarize_test_output` 新增 `adapter` 参数，优先使用语言适配器生成专属摘要（skill / sidecar 已接入）。
  - Go：失败时列出具体失败用例名（`--- FAIL: TestX`，多个用例去重计数）；verbose 模式统计 `--- PASS:` 数量。
  - Rust：失败时从 `failures:` 块提取失败用例名；成功摘要保留 `test result: ok. N passed` 统计。
- 测试：新增远程审计重试 / CA、证据导出、Go / Rust 解析增强用例 13 个（263 → 276）。
- 文档：README 特性 / CLI / 配置 / 安全 / 供应链 / Roadmap 与示例配置同步更新。

## [0.10.1] - 2026-08-30

- 发布流程切换到 PyPI Trusted Publishing（OIDC）：
  - `release.yml` 移除 `PYPI_API_TOKEN` 密码认证，改用 GitHub OIDC 身份换取短时发布 token
    （需在 PyPI 项目设置添加 Trusted Publisher：Owner `Xuqing0415` / Repository `phase-barrier` /
    Workflow `release.yml`）。
  - 发布时同时生成 PyPI 侧 PEP 740 attestations（此前因使用密码认证被忽略）。
- 文档：README 供应链校验命令改为 `.sigstore.json` 签名文件，发布说明更新为 Trusted Publishing。

## [0.9.0] - 2026-08-29

- 审计日志远程推送（SIEM）：
  - 新增 `anti_shortcut.remote_audit.RemoteAuditSink`：零依赖（stdlib `urllib` + `threading` + `queue`），
    后台线程异步批量 POST JSON 审计事件（单事件为对象，多事件为数组）。
  - 配置新增 `audit_remote_url` / `audit_remote_token` / `audit_remote_timeout` /
    `audit_remote_batch_size` / `audit_remote_max_queue` / `audit_remote_flush_interval`；
    队列有界（drop-oldest）、网络失败只计数，绝不阻塞门禁主流程。
  - `get_audit_logger` 支持 `remote` 参数（structlog processor / stdlib handler 双路径）；
    sidecar 新增 `--audit-remote-url` / `--audit-remote-token`，关闭时自动冲刷队列。
- 证据签名：
  - 新增 `anti_shortcut.evidence.EvidenceManifest`：每次阶段推进时把证据文件 SHA-256 写入
    `.agent_gate/evidence_manifest.json`（可选 HMAC 签名，独立于 state.json）。
  - 新增 CLI `verify-evidence`：对照工作区校验证据清单，检测文件缺失 / 事后篡改
    （配置 `state_hmac_key` 后清单带 `sig` 字段，篡改清单本身也会被拒绝）。
- 密钥轮换（HMAC）：
  - `StateManager` 新增 `hmac_keys` 参数与环境变量 `PHASE_BARRIER_HMAC_KEYS`（逗号 / 空白分隔）：
    轮换期接受多个旧密钥用于验证，任何写入自动改用主密钥重新签名。
  - 新增 `StateManager.rotate_key(new_key, keep_old=False)`：校验现有签名后以新密钥重签名；
    未签名状态视为“启用签名”迁移。配置新增 `state_hmac_keys`。
  - 新增 CLI `rotate-key --to <new> [--from <old>] [--keep-old]`。
- 测试：新增远程审计、证据清单、密钥轮换用例 29 个（234 → 263）。
- 文档：README 特性 / CLI / 配置 / 安全 / 模块结构 / Roadmap、示例配置与 K8s 模板同步更新。

## [0.8.0] - 2026-08-29

- Java 输出解析增强：
  - Surefire 汇总行支持 `Skipped` 字段；失败时取最后一次出现的最终汇总（`Results:` 之后），
    避免被首个通过类的统计误导。
  - 新增 Gradle 风格解析（`3 tests completed, 1 failed`）与 JUnit Platform Console 风格
    （`[ N tests successful / failed ]`）。
- 状态签名（HMAC-SHA256）：
  - 配置新增 `state_hmac_key`（或环境变量 `PHASE_BARRIER_HMAC_KEY`）；启用后 state.json 写入
    `signature: v1:<hmac-sha256>`，加载时自动校验。
  - 篡改 / 未签名 / 密钥不匹配 → `TamperedStateError`（继承 `CorruptedStateError`），
    CLI 明确报错并拒绝运行；未配置密钥时行为与旧版本完全一致（向后兼容）。
  - sidecar 新增 `--state-key` 参数（等价于 Secret 注入环境变量）。
- GitHub Action 市场发布：
  - release 工作流新增「创建 GitHub Release」步骤（从 CHANGELOG 提取摘要 + 附加 sdist/wheel），
    每次打 `v*` tag 自动生成 Release，Action 在 GitHub Marketplace 自动上架。
  - README 增加 Marketplace 入口；示例固定 tag 更新到 v0.8.0。
- 测试：新增 Java 输出解析、状态签名用例 14 个（220 → 234）。
- 文档：README 特性 / 安全 / 配置 / Roadmap 与 deploy/k8s 模板同步更新。

## [0.7.0] - 2026-08-29

- JavaScript 输出解析（Vitest / Playwright）：
  - 测试命令识别新增 `vitest` / `playwright test`（含 `npx playwright test`）。
  - 新增 `parse_test_output`：提取 Jest / Vitest 风格摘要（`Tests: N passed` / `Test Files: ...`）
    与 Playwright 风格（`N passed` / `N failed` / `FAIL ...test.js`），退出码非 0 时返回失败摘要。
- 覆盖率门禁：
  - 配置新增 `coverage_threshold`（0-100 百分比）；阶段 4/5 推进时要求测试输出包含覆盖率报告且达标。
  - `summarize_test_output` 自动提取 pytest-cov `TOTAL` 行、`go test -cover` 的 `coverage: N% of statements`、
    jest / vitest --coverage 的 istanbul `All files` 行；报告缺失或低于阈值则拒绝推进。
- K8s sidecar：
  - 新增 `anti_shortcut.sidecar` HTTP 门禁服务（`GET /api/state`、`POST /api/advance`、
    `POST /api/test-run`、`POST /api/source-change`），标准库实现、零额外依赖、线程安全。
  - 新增 `deploy/k8s/` 部署模板：`pvc.yaml`、`gate-keeper.yaml`（初始化 Job）、
    `gate-sidecar.yaml`（agent + sidecar Deployment + Service）与部署说明 README。
- 测试：新增 JS 输出解析、覆盖率门禁、sidecar 用例 21 个（199 → 220）。
- 文档：README 适配器表、覆盖率门禁、K8s 部署、模块结构与 Roadmap 同步更新。

## [0.6.0] - 2026-08-29

- JavaScript 真实解析：
  - 新增 `js_count_tests.cjs` 辅助脚本：用项目内 acorn 解析测试文件，统计 `test` / `it` / `describe` 声明（含 `it.each` / `test.skip` / `describe.each` 等修饰符）与 `expect` / `assert` 断言；acorn 缺失或解析失败（如 TS 语法）时自动回退启发式。
  - `jest --listTests` 升级为 `--json`：解析 `testResults[].name`，兼容含 `undefined` 的宽松 JSON 与旧版按行输出。
  - `validate_tests` 证据新增 `parsers` 字段，记录测试统计来源（acorn / 启发式）。
- Java 项目级编译：
  - 有 `pom.xml` / `build.gradle*` 时改用 `mvn test-compile` / `gradle compileTestJava`（优先 `mvnw` / `gradlew` 包装器），以真实项目编译结果为准；结果按（项目根 + `.java` 指纹）缓存，文件变化自动失效，避免逐文件重复编译。
  - 无构建文件或构建工具缺失时回退单文件 `javac -proc:none`（v0.4.0 行为不变）。
- 示例：新增 `examples/anti_shortcut_go_config.yaml`、`anti_shortcut_rust_config.yaml`（Go / Rust 项目门禁配置模板）与 `examples/github-action/gate-go.yml`、`gate-rust.yml`（安装 `setup-go` / `rust-toolchain` 的 PR 门禁示例，`advance` 模式用真实 `gofmt` / `cargo check` 校验）。
- 打包：`anti_shortcut/languages/js_count_tests.cjs` 纳入 wheel / sdist（`setuptools` package-data）。
- 测试：新增 JS acorn / jest-json、Java 项目级编译用例 13 个（186 → 199）。
- 文档：README 适配器表格、GitHub Action 门禁、多语言章节与 Roadmap 同步更新。

## [0.6.0] - 2026-08-29

- JavaScript 真实解析：
  - 新增 `js_count_tests.cjs` 辅助脚本：用项目内 acorn 解析测试文件，统计 `test` / `it` / `describe` 声明（含 `it.each` / `test.skip` / `describe.each` 等修饰符）与 `expect` / `assert` 断言；acorn 缺失或解析失败（如 TS 语法）时自动回退启发式。
  - `jest --listTests` 升级为 `--json`：解析 `testResults[].name`，兼容含 `undefined` 的宽松 JSON 与旧版按行输出。
  - `validate_tests` 证据新增 `parsers` 字段，记录测试统计来源（acorn / 启发式）。
- Java 项目级编译：
  - 有 `pom.xml` / `build.gradle*` 时改用 `mvn test-compile` / `gradle compileTestJava`（优先 `mvnw` / `gradlew` 包装器），以真实项目编译结果为准；结果按（项目根 + `.java` 指纹）缓存，文件变化自动失效，避免逐文件重复编译。
  - 无构建文件或构建工具缺失时回退单文件 `javac -proc:none`（v0.4.0 行为不变）。
- 示例：新增 `examples/anti_shortcut_go_config.yaml`、`anti_shortcut_rust_config.yaml`（Go / Rust 项目门禁配置模板）与 `examples/github-action/gate-go.yml`、`gate-rust.yml`（安装 `setup-go` / `rust-toolchain` 的 PR 门禁示例，`advance` 模式用真实 `gofmt` / `cargo check` 校验）。
- 打包：`anti_shortcut/languages/js_count_tests.cjs` 纳入 wheel / sdist（`setuptools` package-data）。
- 测试：新增 JS acorn / jest-json、Java 项目级编译用例 13 个（186 → 199）。
- 文档：README 适配器表格、GitHub Action 门禁、多语言章节与 Roadmap 同步更新。

## [0.5.0] - 2026-08-29

- 新增 `GoAdapter`（`anti_shortcut/languages/go.py`）：`gofmt -e` 语法检查（纯语法解析，不触发模块下载 / Go 遥测；Go 工具链缺失返回明确错误）、`func TestXxx(t *testing.T)` + `t.Error` / `t.Fatal` / `assert` / `require` 断言启发式测试统计、`go test` / `go vet` 测试命令识别、`ok pkg` / `FAIL` / `--- FAIL:` 输出解析；自动检测标志文件已覆盖 `go.mod`。
- 新增 `RustAdapter`（`anti_shortcut/languages/rust.py`）：有 `Cargo.toml` 时 `cargo check --message-format short`（不检查 test target，避免阶段 2 测试引用未实现函数导致误拦），无项目时回退 `rustc --edition 2021 --crate-type lib` 单文件检查；`#[test]` / `#[tokio::test]` + `assert!` / `assert_eq!` / `assert_ne!` 启发式测试统计；`cargo test` / `cargo nextest` 命令识别与 `test result: ok / FAILED` 输出解析；自动检测标志文件已覆盖 `Cargo.toml`。
- JavaScript 适配器增强：
  - TypeScript 语法检查优先按项目 `tsconfig.json` 整体检查（`tsc -p <tsconfig> --noEmit`），无 tsconfig 时回退单文件检查，单文件模式下未解析的模块依赖（TS2307 / TS2688 / TS7016）降级为“通过（需完整项目验证）”。
  - 新增 `jest --listTests` 动态发现模式：`adapter_options.test_discovery: jest` 强制启用，或自动探测到项目内 jest（`node_modules/jest` / `node_modules/.bin/jest`）时启用；jest 不可用时返回明确错误，可设 `off` 回退启发式。
  - 启发式升级：支持 `it.each` / `test.skip` / `describe.each` 等声明，匹配前剥离注释与字符串字面量，降低 `console.log('test(...)')` 误判。
- `validate_test_collection` 支持适配器级 `error` 字段（jest 缺失等场景直接返回明确原因）；工作区证据扫描跳过 `target/`（cargo 产物目录）。
- 注册表 / 入口点：内置 `go` / `rust` 适配器，`phase_barrier.languages` 入口点补齐 java / go / rust。
- 测试：新增 JS 增强、Go、Rust 适配器用例 39 个（147 → 186；Rust 真实工具用例在未安装 cargo / rustc 时自动跳过）。
- 示例：`examples/anti_shortcut_js_config.yaml` 增加 `test_discovery` 说明（README 多语言章节同步更新）。

## [0.4.0] - 2026-08-29

- 新增 `JavaAdapter`（`anti_shortcut/languages/java.py`）：`javac -proc:none` 语法检查（JDK 缺失返回明确错误；跨文件依赖未解析时降级为“通过，需完整项目编译验证”）、`@Test` 注解 + JUnit/Hamcrest 断言启发式测试统计、`mvn` / `gradle` / `mvnw` / `gradlew` / JUnit Console 测试命令识别、Maven/Gradle `Tests run: N, Failures: M` 输出解析；自动检测标志文件已覆盖 `pom.xml` / `build.gradle*`。
- Java 语法检查：javac 输出目录优先使用源文件旁的隐藏目录 `.phase-barrier-javac`（规避 Windows 系统 Temp 的 ACL 限制），该目录加入证据扫描跳过名单；javac 输出按 UTF-8 / GBK / cp1252 多编码解码，兼容中文 Windows。
- 测试输出摘要：`summarize_test_output` 支持 Maven/Gradle 风格（`Tests run: N, Failures: M`、`BUILD SUCCESS/FAILURE`），摘要优先取统计行。
- 新增 GitHub Action 门禁（仓库根 `action.yml`）：`inspect`（低于 `expected_stage` 则失败）/ `advance` 两种模式，支持 `workspace` / `config` / `version` / `local` 等输入；示例见 `examples/github-action/gate.yml`；CI 增加 `gate-action` 自测 job 验证通过/拦截两条路径。
- 测试：新增 Java 适配器用例 14 个（133 → 147）。

## [0.3.1] - 2026-08-29

- 拦截器：新增 `dd of=...` 写路径识别；门禁目录保护扩展到路径段匹配（`$HOME/.agent_gate/...`、`/tmp/.agent_gate`、Windows 反斜杠路径）；测试命令关键词兜底支持 `./test` 脚本路径。
- CLI：状态文件损坏 / 版本不兼容时输出明确错误（不再误报“配置无效”）；`inspect` / `advance` 对不存在的工作区友好报错，且不再静默创建目录树。
- 文档：README 增加“反馈与贡献”入口与“自定义语言适配器”四步教程（本地配置 / 打包发布 / 入口点注册 / 可运行示例）。
- 示例：新增 `examples/custom_adapter/`（虚构 `.foo` 语言插件 + demo，演示自定义适配器参与拦截流程）。
- 测试：新增拦截器边界与 CLI 错误处理用例 14 个（119 → 133）。

## [0.3.0] - 2026-08-29

- 新增语言适配层（Language Adapter）：抽象 `LanguageAdapter` 接口（文件识别 / 语法检查 / 测试统计 / 测试命令识别 / 测试输出解析）。
- 内置 `PythonAdapter`（AST + compile，行为与 v0.2.x 完全一致）与 `JavaScriptAdapter`（`node --check` / `tsc --noEmit` + 启发式测试校验）。
- 适配器选择优先级：显式 `language` > 自定义 `language_adapter` > 工作区自动检测（`package.json` / `pom.xml` / `go.mod` / `Cargo.toml` / `pyproject.toml` 等）> 默认 Python。
- 配置新增 `language` / `language_adapter` / `adapter_options` 字段；适配器默认文件模式与 YAML 文件模式自动合并。
- 支持通过 `phase_barrier.languages` 入口点注册第三方自定义适配器；顶层包导出语言 API（`get_adapter` / `detect_language` 等）。
- 测试：新增语言适配层测试 34 个（83 → 117）。

## [0.2.1] - 2026-08-29

- 文档：README 增加 Mermaid 架构图、多语言支持说明、FAQ、Roadmap；清理历史遗留说明。
- 示例：新增 `examples/minimal_agent.py`（最小可运行模拟 Agent 集成示例）与 `examples/anti_shortcut_js_config.yaml`（JS/TS 项目配置）。
- 多语言：修复目录级文件模式（如 `src/**/*.ts`）在绝对路径下无法匹配的问题；非 Python 测试文件改为轻量启发式校验（测试声明数 + 断言关键字），非 Python 实现文件跳过 compile 但要求非空。
- CLI：配置加载失败（文件缺失 / YAML 非法 / 字段类型错误 / 工作区不可写）改为友好报错并返回退出码 1，不再输出堆栈。
- 测试：新增拦截器边界、配置异常、CLI 错误处理、多语言校验用例（64 → 83）。

## [0.2.0] - 2026-08-29

- 发行名改为 `phase-barrier`（与 GitHub 仓库同名），import 包名保持 `anti_shortcut`，CLI 命令保持 `anti-shortcut`。
- 版本改由 `setuptools-scm` 从 git tag 推导，不再手工维护 `pyproject.toml` 与 `__init__.py` 两处版本号。
- CI 增加 `twine check dist/*`；新增 `.gitattributes`（统一 LF）与 `CHANGELOG.md`。

## [0.1.0] - 2026-08-29

- 功能：需求→spec→测试→实现→测试→修复→交付的阶段门禁；`write_file` / `execute_command` 工具拦截；spec / 测试 AST / 实现语法 / 测试运行 / 回归证据校验；JSON 状态机 + 审计日志；Docker 只读卷部署示例。
