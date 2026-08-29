# Changelog

版本号由 git tag 驱动（`setuptools-scm`）：打 `vX.Y.Z` tag 后构建的发行包即为 `X.Y.Z`。

## [0.1.0] - 2026-08-29

- 以 `phase-barrier` 作为发行名发布（与 GitHub 仓库同名）；import 包名保持 `anti_shortcut`，CLI 命令保持 `anti-shortcut`。
- 功能：需求→spec→测试→实现→测试→修复→交付的阶段门禁；`write_file` / `execute_command` 工具拦截；spec / 测试 AST / 实现语法 / 测试运行 / 回归证据校验；JSON 状态机 + 审计日志；Docker 只读卷部署示例。
- 说明：更早的 0.1.0 曾以 `anti-shortcut-skill` 发布到 PyPI。PyPI 不支持项目改名或删除，旧项目将永久保留，建议在 PyPI 上将其 yank。
