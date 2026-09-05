# phase-barrier

[![CI](https://github.com/Xuqing0415/phase-barrier/actions/workflows/ci.yml/badge.svg)](https://github.com/Xuqing0415/phase-barrier/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/phase-barrier.svg)](https://pypi.org/project/phase-barrier/)
[![Python versions](https://img.shields.io/pypi/pyversions/phase-barrier.svg)](https://pypi.org/project/phase-barrier/)
[![Marketplace](https://img.shields.io/badge/Marketplace-Phase%20Barrier%20Gate-blue.svg?logo=github&logoColor=white)](https://github.com/marketplace/actions/phase-barrier-gate)
[![Docs](https://img.shields.io/badge/Docs-MkDocs-blue.svg)](https://xuqing0415.github.io/phase-barrier/)

强制编码 Agent（如 Alpha-SWE）遵循标准工程师 SOP 的**阶段门禁（Stage Gate）**框架。

> 流程：需求 -> spec 设计 -> 测试用例 -> 实现 -> 测试 -> 修复 -> 交付。
> 每个阶段要求可验证证据（spec 章节 / 测试 AST / 语法检查 / 测试结果 / 覆盖率），
> 跳步、偷步、伪造产出会被自动拦截——先有证据，才放行下一步。

## 为什么需要 phase-barrier？

编码 Agent 拿到需求后倾向于“直接写代码”，跳过需求分析、设计与测试，造成理解偏差、
缺回归保障、后期难维护。phase-barrier 在 Agent 的工具调用层加一道闸门：

- **不可绕过**：校验逻辑独立于 Agent 决策循环；状态文件由 Skill 独占原子写入。
- **证据明确**：每个阶段要求具体可验证的产物，拦截时返回可读原因，Agent 可据此修正流程。
- **自动校验**：spec 章节检查、测试 AST 统计、语法检查、测试结果解析、覆盖率门禁全部自动完成。

## 核心特性

- **阶段状态机**：需求 -> spec -> 测试 -> 实现 -> 测试 -> 修复 -> 交付，逐级放行、不可跳级。
- **多语言适配**：Python / JavaScript / Java / Kotlin / Scala / Go / Rust / Ruby / PHP / C++ / C# / .NET。
- **工具拦截**：包装 `write_file` / `execute_command`；shell 重定向、`python -c` / `node -e` 等脚本写入同样受控。
- **防篡改**：HMAC 状态签名、证据清单（SHA-256）、`verify-evidence` Git 基线校验。
- **安全规则包**：shell 注入 / 路径穿越 / 硬编码密钥 / 许可证头等内置规则，YAML 一键启用。
- **集成面广**：进程内包装、CLI 透明代理、GitHub Action、K8s sidecar（Helm）、Docker 一键体验。
- **Agent 框架示例**：LangChain / AutoGPT / SWE-agent 开箱即用示例（`docs/integrations.md`）。
- **插件机制**：语言适配器 / 校验器 / 拦截规则 / 集成插件四类入口点 + `plugin-verify` 自动验证。
- **自洽工程**：CI 全矩阵真实工具链、覆盖率门禁（≥90%）、模糊测试与性能 / SWE-bench 基准。

## 快速开始

### Docker 一键体验（无需安装）

```bash
docker run --rm -it ghcr.io/xuqing0415/phase-barrier-demo
```

演示“跳步被拦截 -> 按 SOP 补齐证据 -> 全通到交付”。

### pip 安装 + 初始化

```bash
pip install phase-barrier
python -m anti_shortcut init          # 自动检测语言，生成带注释的 config.yaml
python examples/minimal_agent.py      # 最小可运行示例：先跳步被拦，再按 SOP 通过
```

### 最小接入（进程内包装）

```python
from anti_shortcut import AntiShortcutSkill

skill = AntiShortcutSkill(
    workspace=".",
    config="anti_shortcut_config.yaml",   # 可选
    user_request="实现一个计算斐波那契数列的函数",
)

# 包装 Agent 工具表：write_file / execute_command / advance_stage 全部经过门禁
tools = skill.install(agent.tools)

tools["write_file"]("fib.py", impl)   # 未完成 spec/测试 -> 抛 PermissionError 并说明原因
tools["advance_stage"](2)             # 校验 spec 证据通过后，进入阶段 2（测试用例编写）
```

可运行示例：[`examples/minimal_agent.py`](examples/minimal_agent.py)（拦截 + 正常流程）、
[`examples/demo.py`](examples/demo.py)（完整演示）。详细步骤见 [docs/quickstart.md](docs/quickstart.md)。

### 命令行门禁（编排器 / CI / 人工复核）

```bash
anti-shortcut inspect --workspace . --json                         # 查看当前阶段
anti-shortcut write --workspace . --path spec.md --content "..."   # 经门禁写文件
anti-shortcut exec --workspace . --command "python -m pytest -q"   # 经门禁执行测试
anti-shortcut advance --workspace . --to 2                         # 推进阶段（校验证据）
```

## 使用示例（输出摘要）

```text
[BLOCKED] write_file(fib.py, ...)
          -> 当前阶段不允许编写实现代码，请先完成 spec 与测试用例
[OK]      write_file(spec.md, ...)
[OK]      advance_stage(2)
...
[demo] 完成：交付
```

## 文档导航

| 主题 | 文档 |
|------|------|
| 快速开始 | [docs/quickstart.md](docs/quickstart.md) |
| CLI 使用 | [docs/usage.md](docs/usage.md) |
| 配置指南（全字段） | [docs/configuration.md](docs/configuration.md) |
| 语言适配器 | [docs/languages.md](docs/languages.md) |
| 架构与设计 | [docs/architecture.md](docs/architecture.md) |
| 拦截规则与安全加固 | [docs/security-rules.md](docs/security-rules.md) |
| 状态与审计（远程推送） | [docs/audit-logging.md](docs/audit-logging.md) |
| GitHub Action | [docs/github-action.md](docs/github-action.md) |
| 编排器钩子 SDK | [docs/orchestrator-hooks.md](docs/orchestrator-hooks.md) |
| Agent 框架集成（LangChain 等） | [docs/integrations.md](docs/integrations.md) |
| K8s 部署（Helm） | [docs/k8s.md](docs/k8s.md) |
| 插件与生态 | [docs/plugins.md](docs/plugins.md) |
| API 参考 | [docs/api.md](docs/api.md) |
| FAQ | [docs/faq.md](docs/faq.md) |
| Roadmap | [docs/roadmap.md](docs/roadmap.md) |
| 发布与供应链安全 | [docs/release.md](docs/release.md) |
| 贡献指南 | [CONTRIBUTING.md](CONTRIBUTING.md) |

## 支持的语言与框架

| 语言 | 适配器 | 测试框架 |
|------|--------|----------|
| Python | PythonAdapter | pytest / unittest |
| JavaScript / TypeScript | JavaScriptAdapter | Jest / Vitest / Playwright / Cypress |
| Java | JavaAdapter | JUnit 4/5 / TestNG / Maven / Gradle |
| Kotlin | KotlinAdapter | JUnit 5 / kotlin.test / Gradle |
| Scala | ScalaAdapter | ScalaTest / MUnit / JUnit / sbt |
| Swift | SwiftAdapter | XCTest / swift-testing（swift test） |
| Go | GoAdapter | testing / testify |
| Rust | RustAdapter | cargo test |
| Ruby | RubyAdapter | RSpec / Minitest |
| PHP | PhpAdapter | PHPUnit |
| C / C++ | CppAdapter | GoogleTest / Catch2 / CTest |
| C# / .NET | CSharpAdapter / DotNetAdapter | xUnit / NUnit / VSTest / dotnet test |
| Dart | DartAdapter | package:test / flutter test |

自动检测规则、自定义适配器与各语言语法检查方式见 [docs/languages.md](docs/languages.md)。

## 社区与支持

- 使用问题 / 功能建议 / Bug：[GitHub Issues](https://github.com/Xuqing0415/phase-barrier/issues)（内置模板）
- 插件提交与生态索引：[docs/plugins.md](docs/plugins.md)
- Roadmap：[docs/roadmap.md](docs/roadmap.md) ｜ 更新日志：[CHANGELOG.md](CHANGELOG.md)
- 官方文档站：[https://xuqing0415.github.io/phase-barrier/](https://xuqing0415.github.io/phase-barrier/)
- 与 [alpha-swe](https://github.com/Xuqing0415/alpha-swe) 双向关联（编排器钩子 SDK 已合并接入）

## License

[MIT](LICENSE)
