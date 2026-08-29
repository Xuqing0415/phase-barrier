# Changelog

版本号由 git tag 驱动（`setuptools-scm`）：打 `vX.Y.Z` tag 后构建的发行包即为 `X.Y.Z`。

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
