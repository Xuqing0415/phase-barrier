# Changelog

版本号由 git tag 驱动（`setuptools-scm`）：打 `vX.Y.Z` tag 后构建的发行包即为 `X.Y.Z`。

版本号由 git tag 驱动（`setuptools-scm`）：打 `vX.Y.Z` tag 后构建的发行包即为 `X.Y.Z`。

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

版本号由 git tag 驱动（`setuptools-scm`）：打 `vX.Y.Z` tag 后构建的发行包即为 `X.Y.Z`。

版本号由 git tag 驱动（`setuptools-scm`）：打 `vX.Y.Z` tag 后构建的发行包即为 `X.Y.Z`。

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
