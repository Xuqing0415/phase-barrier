# Changelog

版本号由 git tag 驱动（`setuptools-scm`）：打 `vX.Y.Z` tag 后构建的发行包即为 `X.Y.Z`。

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
