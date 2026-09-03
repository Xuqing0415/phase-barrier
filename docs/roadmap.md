# Roadmap（规划）

> 迁移自 README 精简版主页；已发布版本的完整条目见 [更新日志](changelog.md)。[返回 README](https://github.com/Xuqing0415/phase-barrier#readme)

- **v0.6.0 已完成**：JavaScript 真实解析（acorn / `jest --listTests --json`）、Java 项目级编译（`mvn test-compile` / `gradle compileTestJava`，`mvnw` / `gradlew` 优先 + 指纹缓存）、Go / Rust GitHub Action 门禁示例与项目配置模板。
- **v0.7.0 已完成**：JS 输出解析覆盖 Vitest / Playwright、覆盖率门禁 `coverage_threshold`（pytest-cov / `go test -cover` / istanbul 表）、K8s sidecar 部署模板与 HTTP 门禁服务（`anti_shortcut.sidecar`）。
- **v0.8.0 已完成**：Java 输出解析增强（Surefire `Skipped` / Gradle / JUnit Console）、状态签名 HMAC（`state_hmac_key` / `PHASE_BARRIER_HMAC_KEY`）、GitHub Action 市场发布（tag 即 Release）。
- **v0.9.0 已完成**：审计日志远程推送（SIEM：`audit_remote_url` + 异步批量 + 队列保护）、证据签名（`evidence_manifest.json` + `verify-evidence`）、HMAC 密钥轮换（`state_hmac_keys` / `rotate-key`，含无签名->启用签名迁移）。
- **v0.10.0 已完成**：审计远程推送增强（`audit_remote_ca_bundle` TLS 自定义 CA、`audit_remote_retries` 指数退避重试）、证据清单导出（`export-evidence`）、sigstore 供应链签名（release 工作流 + `cosign verify-blob`）、Go / Rust 测试输出解析增强（失败用例名提取，`summarize_test_output` 接入语言适配器）。
- **v0.11.0 已完成**：审计远程推送增强（`audit_remote_client_cert` / `audit_remote_client_key` mTLS
  双向 TLS、`audit_remote_headers` 自定义请求头、`audit_remote_spool_dir` 持久化重试队列）、证据清单
  Git 门禁（`verify-evidence --git-base <ref>` + `examples/github-action/evidence-gate.yml`）、
  Ruby / C# 语言适配器（`ruby -c` / `dotnet build` + RSpec / Minitest / xUnit / NUnit 输出解析）。
- **v0.12.0 已完成**：自定义校验器与拦截规则入口点（`phase_barrier.validators` / `phase_barrier.interceptors`，
  含进程内 `register_validator` / `register_rule`，可覆盖内置校验 / 追加拦截规则）、审计远程推送 mTLS
  端到端集成示例（`examples/mtls_audit/`）、GitHub Action Marketplace 上架确认（Phase-Barrier Gate）。
- **v0.13.0 已完成**：拦截器边界与 CLI 错误处理补强（命令注入变体、路径特殊字符、
  门禁目录全写路径防护——含 `&>` / `&>>` / `>|` / `dd of=` / 引号包裹路径；CLI 对损坏状态、
  越界阶段号、证据缺失 / 语法错误给出明确报错；新增 16 个边界测试）、
  自定义校验器 / 拦截规则插件文档与可运行示例（`examples/plugin_rules/`）。
- **v0.14.0 已完成**：脚本类写入检测（`python -c` / `node -e` / `bash -c` 参数内的 `open()` / `Path().write_text` / `fs.writeFileSync` / 重定向写入路径，脚本改代码
  同样受阶段门禁约束）、`verify-evidence` / `export-evidence` 的 CLI 错误处理补强
  （损坏 / 缺字段清单、签名密钥不匹配、缺失工作区、嵌套 `--out` 自动建目录）、
  GitHub Action 门禁输入校验（mode / expected_stage / to / workspace）与 CI 自测扩展。
- **v0.15.0 已完成**：审计远程推送故障告警（`RemoteAuditSink` 新增 `on_failure` 回调与 `metrics()`，`AntiShortcutSkill` 自动把 `audit_remote_failed` 告警写入本地 `audit.log`，避免自喂循环）；`sidecar` HTTP 门禁服务输入校验（bool / 越界阶段号、`output` 类型、`.agent_gate` 路径）。
- **v0.16.0 已完成**：CI 矩阵安装 Node.js / Go / Rust / Ruby 真实工具链，激活 JS/Go/Rust/Ruby 适配器真实工具测试（消除环境跳过盲区）；输出解析与覆盖率门禁边界补强（ANSI 剥离、istanbul 千分位、阈值临界与非法配置值校验、sidecar CLI、Ruby/Rust 真实路径 mock 覆盖，共 29 个新用例）；CI 新增 coverage job（`--fail-under=90`，核心包覆盖率 90%），项目自身吃自己的狗粮。

- **v0.17.0 已完成**：K8s sidecar 透明代理——sidecar 新增 `POST /api/write` / `POST /api/exec`，路径限定工作区 / 拒绝 `.agent_gate` / 按阶段拦截写入与测试命令，exec 自动记录测试摘要；超时后终止进程树并立即返回；新增 Agent 侧 `GateClient`（仅标准库 urllib）与 `examples/k8s_proxy/` 最小示例；`deploy/k8s/` 清单更新（镜像 0.17.0）；新增 30 个透明代理测试（415 -> 445）。

- **v0.18.0 已完成**：CLI 透明代理命令 `write` / `exec`（经门禁写文件 / 执行命令，测试命令自动记录，被拦截退出码 2，`--json` 结构化输出）；GitHub Action 新增 `mode: exec` 与 `command` 输入，可在 CI 经门禁执行测试命令；新增 16 个 CLI 门禁测试（445 -> 461）与 CI action 自测扩展。

- **v0.19.0 已完成**：透明代理审计事件（5 类：写成功 / 写拒绝 / 执行成功 / 执行拒绝 / 执行超时），每条事件携带阶段摘要写入本地 `audit.log` 并推送远端 SIEM（原 `proxy_file_written` 更名为 `proxy_write_ok`）；CLI `exec`、sidecar `/api/exec`、`GateClient` 三端支持 `cwd` 工作目录参数（限定工作区内）；新增 12 个代理审计与 cwd 测试（461 -> 473）。

- **v0.20.0 已完成**：sidecar 审计查询 API（`GET /api/audit?limit=50&event=...`，按时间倒序 + 数量上限 + 事件过滤）与 `GateClient.audit()`；新增 `python -m anti_shortcut sidecar` 统一 CLI 入口（K8s 清单 / 文档切换，镜像 0.20.0）；端到端审计链测试（HTTP 写拒绝 -> 本地 audit.log -> /api/audit -> 远端 SIEM）；新增 11 个测试（473 -> 484）。

- **v0.21.0 已完成**：审计查询分页（`offset`）与时间范围过滤（`since` / `until`，ISO 时间戳，含端点），`GET /api/audit` 响应增加 `total` / `offset` 元信息；`GET /api/verify-evidence` 与 `GateClient.verify_evidence()` 远程校验证据清单；sidecar 入站 mTLS 访问控制（`--tls-cert` / `--tls-key` / `--tls-client-ca`，`GateClient(cert=..., ca=...)`），示例 `examples/mtls_sidecar/`；新增 10 个测试（484 -> 494）。

- **v0.22.0 已完成**：编排器钩子 SDK（`PhaseBarrier`，供 Alpha-SWE 等平台在任务启动 / 阶段切换钩子调用：`check` 只读校验放行 / 拦截 / 跳步，`advance` 复用 `advance_stage` 证据校验，`record_test_run` 登记测试结果，`verify_evidence` 统一 `ok=False` 异常处理）；CLI 新增 `check` 子命令；编排器集成示例 `examples/orchestrator_hooks/`；README 交叉引用 alpha-swe；新增 28 个测试（494 -> 522）。
- **v0.23.0 已完成**：Java 适配器输出解析增强——失败用例提取（Surefire `<<< FAILURE!` / Gradle `> FAILED` / JUnit Console `MethodSource`，去重上限 50）与 Gradle `skipped` 统计；GitHub Action 元数据增强——新增 `mode: check` 只读门禁校验（`stage` 输入）、`exec` 模式 `cwd` 工作目录输入；CI 自测新增 check 模式放行 / 拒绝 / 缺参路径；新增 6 个 Java 解析测试（522 -> 528）。
- **v0.24.0 已完成**：Java 输出解析剩余项——Surefire 参数化用例（displayName 含逗号 / `[N]` 序号）与
  `<<< ERROR!` 超时 / 异常细分（`TimeoutException` / `timed out` 判定「超时」）；Gradle `> SKIPPED` 兜底计数、
  `BUILD SUCCESSFUL` 汇总与多模块 reactor 聚合（`N tests completed, M failed` 求和）；JUnit Platform Console
  `MethodSource` 嵌套格式（`Class.method(ParameterizedTest)[N]`）；测试命令识别补充 Windows wrapper
  （`mvnw.cmd test` / `gradlew.bat test` / `.\mvnw`）；新增 10 个 Java 解析边界测试（528 -> 538）。
- **v0.25.0 已完成**：GitHub Action 市场元数据增强——`action.yml` 增加 `outputs` 声明（`workspace` / `stage` / `allowed`），门禁步骤可通过 `steps.gate.outputs.*` 供下游复用；示例更新至 `@v0.25.0` 并补充 outputs 用法与参数联动说明；CI 升级 checkout@v7 / setup-python@v7 / setup-node@v7 / setup-go@v7 / upload-artifact@v7 与 action-gh-release@v3（消除 Node 20 弃用告警）并新增 gate outputs 断言；新增发布到 GitHub Marketplace 的流程文档 `docs/publish-to-marketplace.md` 与 action 元数据测试 `tests/test_action_meta.py`。
- **v0.25.1 已完成**：composite action `outputs` 修复——三个输出（`workspace` / `stage` / `allowed`）补上 `value: ${{ steps.gate.outputs.* }}` 映射（仅写 `$GITHUB_OUTPUT` 不会传播到调用方，v0.25.0 的 gate-action 自测因此读到空值）；action 内部 `setup-python@v7` 消除 Node 20 弃用告警；README 示例同步至 `@v0.25.1`。
- **v0.26.0 已完成**：产品化与生态建设——`python -m anti_shortcut init` 配置脚手架与全字段配置指南 `docs/configuration.md`；Docker 一键体验镜像（`ghcr.io/xuqing0415/phase-barrier-demo`）；C++ / .NET 适配器（`CppAdapter` / `DotNetAdapter`，含 GoogleTest / VSTest 输出解析与自动检测）；PR 增量校验（`verify-evidence --git-base` 的 `git_impact` 映射 + Action `mode: verify` / `git_base` 输入，示例 `examples/github-action/gate-pr.yml`）；内置安全规则包（`no_shell_injection` / `no_path_traversal` / `no_hardcoded_secrets` / `require_license_header`）；插件索引 `docs/plugins.md`、贡献指南 `CONTRIBUTING.md` 与 Issue 模板。
- **v0.26.3 已完成**：多 Agent 并发任务共享门禁状态——`StateManager` 跨进程文件锁
  （POSIX `flock` / Windows `msvcrt`）+ 写前重载 + 唯一临时文件原子替换，并发推进不丢更新、
  状态文件不损坏；`PhaseBarrier.refresh()` 重载状态与证据清单，编排器轮询可见他人推进结果；
  新增多 Agent 并发示例 `examples/orchestrator_hooks/multi_agent.py`
  （3 个并发 Agent 协作 + 6 路并发 `record_test_run` 写入压力演示，CI 端到端执行）。
- **v0.26.2 已完成**：编排器 SDK 辅助查询——`PhaseBarrier.list_stages()`（阶段清单：编号 / 名称 / 准入门槛 / 必需证据，元数据集中定义于 `config.STAGE_META`）与 `PhaseBarrier.stage_of(path)`（spec->1 / test->2 / source->3 / other->None，与 `verify-evidence --git-base` 的 `git_impact` 分类一致）；`docs/plugins.md` 收录第一批官方示例插件索引。
- **v0.27.0 已完成**：K8s 生产级部署 —— `deploy/helm/phase-barrier/` Helm chart（sidecar + agent 双容器、PVC/emptyDir、mTLS / HMAC / 审计、gate-keeper Job）与 `kind` 端到端测试进 CI；LangChain / AutoGPT / SWE-agent 框架集成示例（`examples/*_integration/` + `docs/integrations.md`）；性能基准 `benchmarks/bench.py`（并发状态写入 + sidecar HTTP 写/执行延迟与吞吐，CI 性能回归门禁）。

- **v0.28.0 已完成**：新增 PHP 适配器（`PhpAdapter`，`php -l` 语法检查 + PHPUnit 启发式，
  `OK (N tests)` / `Tests: N, Failures: M` 输出解析，`composer.json` 自动检测）；C/C++ 适配器增强
  （支持 `.c` 文件与 `gcc -fsyntax-only`、Catch2 `TEST_CASE` / `SCENARIO` 宏与 `REQUIRE*` / `CHECK*`
  断言、Catch2 输出解析）；现有适配器测试框架增强（Java TestNG `Total tests run:` 汇总、
  Python `unittest.TestCase` 的 `self.assert*` 断言计数、JS Cypress `cy.should` 断言与
  `npx cypress run` 命令 / `All specs passed!` 输出）；CI 安装 PHP 激活真实工具用例。

- **v0.29.0 已完成**：插件生态自动化——`python -m anti_shortcut plugin-verify` 自动验证四类插件
  入口点（语言适配器 / 校验器 / 拦截规则 / 集成插件），CLI `--json` 供 CI 断言；插件 CI 模板
  `.github/actions/plugin-test/`（composite action）+ 参考工作流 `.github/workflows/plugin-test.yml`，
  插件仓库复制即用；CI 新增 `plugin-verify` 自测 job（本地安装示例插件端到端验证）。
  官方文档站：MkDocs + Material 主题（`docs/` 下 index / quickstart / usage / configuration /
  integrations / plugins / k8s / contributing / changelog），`.github/workflows/docs.yml`
  构建并推送 `gh-pages` 分支，启用 Pages 后访问 https://xuqing0415.github.io/phase-barrier/ 。

- **v0.30.0 已落地**：SWE-bench 门禁基准脚本化——新增 `benchmarks/swe_bench_gate.py` 模拟 SWE-bench 风格任务驱动 `AntiShortcutSkill`，统计 SOP 合规率 / 跳步拦截率 / 证据修复率 / resolve 率，支持 `--json` / `--fail-fast` 阈值门禁并纳入 CI bench job；配套冒烟测试 6 个与教程“脚本化基准”章节。

- **v0.31.0 已落地**：性能与安全加固——新增解析器模糊测试基准 `benchmarks/fuzz_parsers.py`（8 个目标：输出摘要 / 覆盖率提取 / 写路径提取 / 门禁目录探测 / Java 解析器 / 适配器文件识别 / 测试命令识别，确定性种子复现），并纳入 CI bench job；新增 `.github/workflows/security.yml` 依赖漏洞扫描（`pip-audit` 完整环境 + `osv-scanner` manifest，每周定时 + push/PR 触发）；配套冒烟测试 7 个。
- **v0.32.0 已完成**：主流 Agent 框架集成收尾——LangChain 新增 `PhaseBarrierTool`（`BaseTool` 子类，
  `examples/langchain_integration/phase_barrier_tool.py`）与最小演示 `demo.py`，CI 新增 `integration-langchain`
  job（固定安装 `langchain-core>=0.3,<0.5` 验证真实 BaseTool 路径）；新增 Kotlin 语言适配器
  （`KotlinAdapter`：`kotlinc` 语法检查 + JUnit5/kotlin.test 启发式，复用 Java Gradle/Surefire 输出解析，
  注册 `language: kotlin` 与 `src/main/kotlin` 自动探测）；文档站新增 API 参考页；多语言文档清单同步。

- **v0.32.1 已完成**：文档站部署并发修复（`.github/workflows/docs.yml` 改为全工作流共享静态锁
  `docs-deploy`，消除 main 推送与 v* 标签推送并发抢推 gh-pages 的撞车）。
- **v0.32.2 已完成**：README 精简重构（约 150 行）并按主题迁移到 9 个新 docs 页，MkDocs 导航重组；
  全仓 emoji / 符号字形 ASCII 清理。

**规划中（Next）**

- Kotlin 真实工具链：CI 暂未内置 kotlinc，`tests/test_kotlin_adapter.py` 的真实语法用例在本地具备 kotlinc 时执行；可按需在 ubuntu runner 安装 `kotlin` 包激活。
- `phase-barrier-plugin-template` 独立模板仓库（把插件索引自动化托管到仓库级，`docs/plugins.md` 已含 CI 模板与提交流程）。
版本按 tag 驱动发布（`git tag vX.Y.Z && git push origin vX.Y.Z`），每次发版更新 CHANGELOG。
