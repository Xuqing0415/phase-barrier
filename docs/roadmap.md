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

- **v0.33.0 已完成**：官方插件模板仓库 [phase-barrier-plugin-template](https://github.com/Xuqing0415/phase-barrier-plugin-template) 落地——独立仓库提供四类入口点示例
  （.demo 语言适配器 / 阶段 1 校验器 / vendor 拦截规则 / 集成插件）、`examples/demo.py` 端到端演示、
  CI（plugin-verify + pytest）；`docs/plugins.md` 收录模板仓库并更新提交流程与 CI pin 版本。

- **v0.33.1 已完成**：Kotlin 真实工具链进 CI——test / coverage job 安装 JDK 17 + kotlinc 2.2.0，
  `tests/test_kotlin_adapter.py` 的真实语法用例（kotlinc 成功 / 报错两条路径）在 CI 强制运行，
  不再按环境跳过。

- **v0.33.2 已完成**：修复 v0.33.1 误用不存在的 `actions/setup-java@v7` 导致 job 无法解析的问题，
  回退到长期稳定的 `actions/setup-java@v4`（temurin 17），kotlinc 安装与真实语法用例照常执行。

- **v0.33.3 已完成**：修复 kotlinc 安装步骤的假自检——`$GITHUB_PATH` 只对后续 step 生效，
  安装 step 内裸调 `kotlinc -version` 打印的是 runner 预装 kotlinc（2.4.10）而非下载的 2.2.0；
  改为解压前 `sudo rm -rf /opt/kotlinc` 清理 + 绝对路径 `/opt/kotlinc/bin/kotlinc -version` 打印 
  并 grep 断言 `2.2.0`；test / coverage job 的 `actions/setup-java@v4` 同步升级 `@v5`。


- **v0.34.0 已完成**：新增 Scala 语言适配器（`ScalaAdapter`：scalac 单文件语法检查 + JUnit / ScalaTest / 
  MUnit / spec2 启发式统计，输出解析 ScalaTest 优先、回退 Java Surefire / Gradle / JUnit Console；
  `build.sbt` 自动探测）；C# / .NET 与 Scala 真实工具链进 CI（.NET SDK 8 + scala-2.13.18，绝对路径
  版本断言），激活 CSharpAdapter / ScalaAdapter 真实语法用例；解析器模糊测试目标 8 -> 10
  （新增 C# / Scala 输出解析目标）。

- **v0.34.1 已完成**：gRPC 教程降级为规划草案（顶部醒目警告 + MkDocs 导航 / 首页标题标注
  「规划中」，与代码事实一致）；新增 `.github/workflows/plugin-check.yml` 每周一自动对
  `examples/custom_adapter` 与 `examples/plugin_rules` 两个官方示例插件运行
  `plugin-verify --json` 并上传报告 artifact；`.gitattributes` 显式列出常见文本类型统一 LF，
  CONTRIBUTING 记录 Windows `git add` 换行规范化提示属正常现象。

- **v0.35.0 已完成**：sidecar HTTP 边界模糊 + 多进程并发锁压力基准 `benchmarks/fuzz_sidecar.py`
  （真实 HTTP 服务器随机请求 0 崩溃；持锁原子递增 + 持锁异常退出验证 OS 自动释放锁）并纳入
  CI bench job；修复模糊暴露的 sidecar HTML 501（`send_error` 改 JSON）与
  `execute_command` Popen 启动失败未捕获（转 `ProxyError`）两个问题；`benchmarks/bench.py`
  新增可选 p99 阈值门禁并在 CI 启用（报告 artifact 上传）。

- **v0.36.0 已完成**：Windows CI 核心测试矩阵——`windows-latest` × Python 3.12/3.13 新增
  `test-windows` job 运行全量 pytest（真实工具链不额外安装、对应用例按 skipif 自动跳过，
  ubuntu job 全量激活），覆盖 `msvcrt` 文件锁、路径分隔符与 CLI / sidecar / audit 等核心路径；
  Windows 本机全量 pytest 预演通过（0 失败）。


- **v0.37.0 已完成**：新增 Swift 语言适配器（`SwiftAdapter`：`swiftc -typecheck` 单文件
  语法检查——脚本模式对 `@main` 文件报错自动以 `-parse-as-library` 重试、跨文件/依赖缺失
  降级为“需完整项目编译验证”、swiftc 缺失返回明确错误；文件识别排除 `Package.swift` 清单；
  XCTest `func testXxx()` + swift-testing `@Test` 启发式统计，先剥离注释/字符串避免误判；
  输出解析 XCTest `Executed N tests, with M failures` / swift-testing `Test run with N tests
  passed|failed` / xcodebuild `** TEST SUCCEEDED/FAILED **`）；`Package.swift` 自动探测；
  解析器模糊测试目标 10 -> 11（新增 Swift 输出解析目标）；CI 安装 swift.org ubuntu24.04
  工具链（swift-6.1.2，绝对路径版本断言），激活 SwiftAdapter 真实语法用例；补齐 v0.34.0
  遗漏的 `scala` 语言入口点并新增 `swift` 入口点；`.build`（SwiftPM 构建产物）加入
  工作区遍历跳过目录。

- **v0.38.0 已完成**：macOS CI 核心测试矩阵——`test-macos` job（`macos-latest` ×
  Python 3.12/3.13）运行全量 pytest；macOS runner 自带 Xcode CLT，`swiftc` 开箱可用，
  SwiftAdapter 真实语法用例在 macOS 天然激活（无需下载工具链）；其余真实工具链不装、
  按 skipif 跳过（ubuntu 全量激活）。核心 pytest 现覆盖 Linux / Windows / macOS 三平台
  （POSIX flock 与 msvcrt 文件锁均在 CI 实测）。

- **v0.39.0 已完成**：K8s sidecar gRPC 服务——`anti_shortcut/proto/sidecar.proto` 定义
  `service PhaseBarrier`（8 个 RPC 与 HTTP API 一一对应：GetState / Advance / RecordTestRun /
  RecordSourceChange / WriteFile / ExecCommand / VerifyEvidence / QueryAudit）；生成代码随包分发
  （`anti_shortcut/proto/`，重生成脚本 `scripts/gen_grpc.sh`）；`anti_shortcut.grpc_service` 实现
  servicer 并复用 GateSidecar 业务逻辑（状态推进 / 证据校验 / 透明代理拦截 / 审计），拦截返回
  `PERMISSION_DENIED`、参数非法返回 `INVALID_ARGUMENT`、推进未通过返回 `FAILED_PRECONDITION`；
  可选依赖 `phase-barrier[grpc]`（dev extra 同步引入，CI 全量跑 gRPC 测试）；新增
  `tests/test_grpc_service.py` 8 个 in-process gRPC 用例；教程
  `docs/tutorials/k8s-sidecar-grpc.md` 由“规划草案”改写为“已实现”。

- **v0.40.0 已完成**：Dart 语言适配器——`DartAdapter`（`anti_shortcut/languages/dart.py`）：
  `*.dart` / `lib/**` / `bin/**` / `web/**` 为实现，`*_test.dart` / `test/**` /
  `integration_test/**` 为测试；语法检查 `dart format --output=none`（纯解析不落盘，
  SDK 缺失返回明确错误）；package:test 启发式统计（`test()` / `testWidgets()` 声明 +
  `expect` 断言，剥离注释与字符串）；测试命令 `dart test` / `flutter test` /
  `dart run test` / `pub run test`；输出解析 `+N: All tests passed!` /
  `+N -M: Some tests failed.`（含 `~K` 跳过）。`pubspec.yaml` 自动探测，注册表 /
  入口点 / 文档同步；解析器模糊测试目标 11 -> 12；CI test / coverage job 安装
  `dart-lang/setup-dart` 激活真实语法用例（通过 / 报错两条路径）；新增
  `tests/test_dart_adapter.py` 19 个用例（2 个真实用例按 dart 是否安装启用）。
- **v0.41.0 已完成**：真实 SWE-bench 评测 harness —— 新增 `benchmarks/swebench_runner.py`（官方同构实例清单 + 基线 / 门禁双组命令模板 + stdout 标记聚合 resolve 率 / 拦截率 / 耗时；`--synthetic` 确定性冒烟与 `--fail-fast` 阈值门禁；不内置官方 swebench 容器与隐藏测试打分，真实运行需用户环境，教程见 `docs/tutorials/swe-bench-real.md`）；新增 `tests/test_swebench_runner.py` 11 个用例；CI bench job 追加合成冒烟步骤。Alpha-SWE 上游接入同步落地（`Xuqing0415/alpha-swe` PR #3 已合并，原待办移除）。
- **v0.41.1 / v0.41.2 已完成（coverage 门禁修复）**：v0.40.0 / v0.41.0 全量 CI 覆盖率 88% < 90%，暴露 gRPC 业务代码未被实测的盲区 —— gRPC handler 跑在 worker 线程，默认 coverage 不按线程追踪（`[tool.coverage.run]` 增加 `concurrency = "thread"`），并新增 `tests/test_grpc_service_direct.py` 8 个主线程直调用例（`grpc_service.py` 覆盖率 17% -> 92%）；进一步定位 v0.39.0 起 gRPC 测试在 CI 一直静默跳过的根因：pb2 生成代码依赖 `google.protobuf`，但 `grpc` / `dev` extra 未声明，已补 `protobuf>=7.35.1`，且两个 gRPC 测试模块对非 grpcio 缺失的导入异常直接抛出。修复后 CI coverage job 为 `850 passed, 0 skipped`、`grpc_service.py` 92%、TOTAL 90% 过门禁。
- **v0.42.0 已完成**：Windows CI 真实工具链激活与回归门禁时间戳修复 —— `test-windows` job（Python 3.10-3.14 矩阵）更名为 `pytest (Windows ...)`，安装 Node 20 / Go 1.22 / Rust stable / Ruby 3.3 / PHP 8.3 / JDK 17 + kotlinc 2.2.0 / .NET 8 / Scala 2.13.18 / Dart 工具链并加入 PATH（PowerShell 下载 kotlinc / scala zip），Windows 上 JS/Go/Rust/Ruby/PHP/Java/Kotlin/.NET/Scala/Dart 适配器真实工具用例不再跳过；Swift 无 Windows 工具链，对应真实用例按 skipif 跳过。同时修复回归门禁的时间戳并发平台差异（Windows CI 偶发失败）：测试运行与源码修改落在同一时钟粒度时门禁失败关闭（`validate_retest` 比较改 `ran_at <= changed_at`，`advance_stage` 4->6 直达分支改 `ran_at > changed_at`），`mark_source_change` 支持注入 `at_epoch` 使顺序单测确定性化，并新增同时间戳失败关闭边界用例。

- **v0.42.1 已完成**：Windows 状态文件并发写入修复 —— `StateManager.__init__` / `reload()`
  改为持伴生文件锁读取（Windows 读句柄默认不共享删除，无锁读会阻塞并发写线程的
  `os.replace`，表现为 CI 偶发 `test_bench_state_contention_smoke` 23/24）；
  `os.replace` 增加短退避重试抵御瞬时共享冲突。

- **v0.43.0 已完成**：macOS CI 真实工具链激活 —— `test-macos` job（Python 3.12 / 3.13）
  更名为 `pytest (macOS ...)`，安装 Node 20 / Go 1.22 / Rust stable / Ruby 3.3 / PHP 8.3 / 
  JDK 17 + kotlinc 2.2.0 / .NET 8 / Scala 2.13.18 / Dart（kotlinc / scala 下载到 runner temp 
  并加入 PATH）；Swift 由 Xcode CLT（swiftc）原生激活。JS/Go/Rust/Ruby/PHP/Java/Kotlin/
  C#/.NET/Scala/Dart 适配器真实工具用例在 macOS CI 强制执行。至此 Linux（v0.16.0 起）/ 
  Windows（v0.42.0 起）/ macOS（v0.43.0 起）三平台真实工具链全量激活。

- **v0.43.1 已完成**：Windows 证据清单并发读写修复 —— v0.43.0 CI `pytest (Windows 3.12)`
  偶发 `test_run_fuzz_http_only_smoke` 500，根因同 v0.42.1：`EvidenceManifest` 无锁读
  `evidence_manifest.json` 会阻塞并发推进线程的 `os.replace`；修复为初始加载 / `reload()` /
  `record()` 写前重载 / 查询快照全部持伴生文件锁（`evidence_manifest.json.lock`）并改用
  `_replace_with_retry` 重试；新增并发回归测试 `test_concurrent_record_reload_safe`。

- **v0.44.0 已完成**：Java 适配器输出解析剩余项与框架矩阵
  回归（修正 Surefire `[INFO]` 前罀 / TestNG 拼写差异 / Gradle 括号与参数化
  行 / JUnit Console 判定，新增 10 组 fixtures 矩阵测试）+ GitHub Action
  Marketplace 元数据增强（英文关键词描述 / author / 参数说明 +
  元数据测试）。

- **v0.45.0 已完成**：编排器 SDK 辅助查询扩展（`PhaseBarrier` 新增
  `get_required_evidence` / `get_last_test_run` / `get_stage_history` /
  `has_uncommitted_changes` 四个只读查询，demo / API 文档 / 13 个单测同步）+ 插件
  生态自动验证流程（根目录 `plugins.json` 索引 + `scripts/verify_plugins.py`
  （安装 -> 子进程 plugin-verify -> 断言入口点 -> `--update` 写回状态）+
  `plugin-verification.yml` 每周二自动验证并提交变更 + 20 个脚本单测）。

- **v0.45.1 已完成**：编排器辅助查询 CLI 化（`anti-shortcut query` 子命令：
  `--required-evidence` / `--last-test-run` / `--stage-history` /
  `--has-uncommitted-changes`，`--json` 即 SDK 返回结构，API / hooks 文档同步）+ 插件索引
  状态表自动同步（`verify_plugins.py --sync-docs` 渲染 `docs/plugins.md` 表格，
  `plugin-verification.yml` 联动提交）。

- **v0.45.2 已完成**：文档一致性收敛——README 支持语言表补 Dart 行（v0.40.0 遗漏）、
  docs/languages.md 补 RubyAdapter 行（v0.11.0 遗漏）、docs/faq.md 内置适配器清单更新为
  13 种语言；docs/best-practices.md 移除对已精简 README“长期规划”章节的过时引用，改为
  指向已落地事实（security.yml 漏洞扫描 / fuzz 基准 / 供应链示例）；`query` CLI 版本标注
  统一为 v0.45.1（help / docstring / api.md / orchestrator-hooks.md）；历史博客加更新注记。

- **v0.46.0 已完成**：第三方插件仓库自动轮询——`plugins.json` 顶层升级为容器
  （`plugins` + `auto_discovery`，旧顶层数组格式兼容读入 / 写回自动升级，条目新增
  `auto_discovered` / `last_commit_sha`），新增 `scripts/auto_discover_plugins.py`
  （按 GitHub topic `phase-barrier-plugin` 搜索 -> 去重 -> `git clone --depth 1`
  -> `pip install -e` -> `plugin-verify --json`，`--dry-run` / `--update` 收录 +
  docs 同步，过滤 fork / archived，Search 限流 403 可降级不中断），周期 workflow
  `plugin-verification.yml` 改为每周一 03:00 UTC「先自动发现再全量验证」两步并上传
  双报告；docs/plugins.md / CONTRIBUTING.md 补自动收录流程，17 个自动发现单测。

- **v0.46.1 已完成**：自动发现入口点归属修复——手动触发工作流首次实跑（不等周一
  cron）暴露 `clone_verify` 会把环境中 phase-barrier 内置语言适配器入口点误归属给
  候选插件；新增按 `direct_url.json` 定位候选发行版的 `_entry_points_of_workdir()`，
  只统计候选自声明入口点并与 plugin-verify ok 结果求交（+3 单测，共 20 个全绿），
  据此重新收录首个自动条目。

- **v0.47.0 已完成**：插件状态页接入文档站（新增 `docs/plugin-status.md`，
  `--sync-docs` 默认同步目标迁移，状态表加「收录 / 提交」列，plugins.md 改指南并
  链接）+ 自动发现条目增量刷新（`git ls-remote` 对比 `last_commit_sha`，SHA 变化
  才重新验证并更新入口点；失败标记 `failed` 且保留旧 SHA 供重试；+7 单测）。

- **v0.48.0 已完成（遗留项一次性收口）**：自定义域名文档 + 非阻塞 CI 检查
  （`docs/custom-domain.md` / `scripts/check_custom_domain.py`，当前未启用属可选）；
  模拟第三方插件 fixture（`tests/fixtures/plugin_alpha/`）+ 自动发现 E2E 4 用例
  （发现收录 / 新提交刷新 / 失败保留 SHA 并重试 / 失败不收录）；
  `verify_plugins.py --sync-only` 接入 docs 构建（状态页与索引自动一致）+
  文档一致性测试 4 用例；视频教程模板 `docs/video-tutorial-template.md`；
  社区推广包 `docs/promotion/README.md`；Alpha-SWE #3 上游跟踪记录；
  新增 14 个单测，全量 953 用例通过。

## 待办 / 已知缺口（截至 v0.48.0）
- 内部可执行项已清零（CI 平台矩阵真实工具链全量激活、插件自动发现 / 增量刷新 /
  状态页自动维护、E2E 演练、推广与视频模板、上游跟踪记录均已落地）。
- 仍属外部 / 资源依赖、无法在本仓库单方面清零：
  - 自定义域名 `docs.phase-barrier.dev` 需用户持有域名并配置 DNS（未启用，可选）；
  - 真实第三方插件数量依赖社区采用（工具与流程就绪，索引目前含官方模板仓库 1 条
    自动条目）；
  - Alpha-SWE 上游 PR #3 已合入（2026-08-30，合并提交 128e6a4，见 docs/integrations.md），
    该项不再阻塞，剩余仅为跨仓库推广执行；
  - 大规模 SWE-bench 评测与视频 / 推广实际执行为资源型长期项（harness / 模板已备）。

版本按 tag 驱动发布（`git tag vX.Y.Z && git push origin vX.Y.Z`），每次发版更新 CHANGELOG。
