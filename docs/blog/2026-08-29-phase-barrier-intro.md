---
title: 给编码 Agent 装上"红绿灯"：phase-barrier 反捷径校验 Skill 的设计与实践
date: 2026-08-29
tags: [AI Agent, 软件工程, 工程效能, Python, 开源]
---

# 给编码 Agent 装上"红绿灯"：phase-barrier 反捷径校验 Skill 的设计与实践

> 一句话：把"需求 -> spec 设计 -> 测试用例 -> 实现 -> 测试 -> 修复 -> 交付"变成一道 Agent 绕不过去的门禁。

> **更新（2026-09-05，v0.45.x）**：本文写于 2026-08-29，内容为 v0.3 时代快照（当时仅内置 Python / JS 两个适配器、119 个测试）。当前已内置 13 种语言的适配器（Python、JS/TS、Java、Kotlin、Scala、Go、Rust、Ruby、PHP、C/C++、C#/.NET、Swift、Dart），测试 800+；能力清单与版本差异见 [更新日志](../changelog.md)。

## 为什么需要"反捷径"

以 Alpha-SWE 为代表的编码 Agent 已经很擅长"把需求变成代码"。但越擅长，越容易暴露一个工程问题：**Agent 会跳过关键工程步骤**。

接到需求后，Agent 的默认倾向是直接写实现代码。需求有没有理解对？接口怎么设计？边界条件是什么？测试怎么保证回归？这些问题往往被一笔带过。快速产出的代码可能能跑，但埋下了三类隐患：

- **需求理解偏差**：没有需求分析，实现和预期对不上，返工成本高；
- **缺乏回归保障**：没有测试用例，后续任何改动都可能悄悄破坏行为；
- **维护困难**：没有设计文档，代码的"为什么"不可追溯。

人写代码有 Code Review 兜底，Agent 写代码却没有。phase-barrier 要做的，就是给 Agent 装上"红绿灯"：**不满足当前阶段的证据要求，就不允许进入下一阶段**。

## 核心思路：阶段门禁（Stage Gate）

phase-barrier 是一个嵌入 Agent 工具调用层的校验 Skill，核心是三件事：

```
阶段状态机（当前在哪个阶段，状态持久化到 .agent_gate/state.json）
    +
证据校验（每阶段必须有可验证的产物，自动检查）
    +
工具拦截（write_file / execute_command 被包装，违规调用直接拒绝）
```

它把开发流程切成 7 个阶段：

| 阶段 | 名称 | 必需证据 | 校验方式 |
|------|------|----------|----------|
| 0 | 需求接收 | 用户需求原文 | 系统自动记录 |
| 1 | Spec 设计 | `spec.md` 包含必需章节 | 文件存在 + 章节模式匹配 |
| 2 | 测试用例编写 | `test_*.py` 测试函数 + 断言 | 文件存在 + AST 静态分析 |
| 3 | 实现代码 | 源代码文件 | 文件存在 + 语法检查（`py_compile`） |
| 4 | 运行测试 | 测试命令退出码 0 + 结果摘要 | 命令日志 + 退出码解析 |
| 5 | 修复与回归 | 修改后再次全部通过 | 同阶段 4，且要求全部通过 |
| 6 | 交付 | 交付摘要 | 自动生成 |

关键设计决策是**校验独立于 Agent 的决策循环**：Agent 想不想遵守 SOP 不取决于它的"自觉"，而取决于它手上的工具是否允许。工具被包装过、状态文件被保护，Agent 无法通过自然语言指令绕过——这是它与"在 Prompt 里写请遵循流程"的本质区别。

## 设计原则与权衡

1. **不可绕过**：校验逻辑放在工具层，Agent 没有原始 `write_file` 的访问权；状态文件目录对 Agent 只读（生产环境用 Docker 只读卷挂载）。
2. **最小侵入**：只包装 `write_file` / `execute_command` 两个工具，注入一个 `advance_stage`，不改 Agent 核心。
3. **证据明确**：拒绝"我觉得写完了"这种模糊判断，要求文件、AST 统计、退出码等硬证据。
4. **自动校验**：所有检查由脚本完成，不需要人工值守。
5. **可配置**：YAML 配置可调阈值、文件模式、测试命令正则；甚至可以关掉某道门禁（但"一键关闭全部"与设计目标相悖，不支持）。

最有意思的权衡是"防跳步"和"防质量"的分界。phase-barrier 能保证**流程被走完**，但保证不了**测试写得好**——Agent 完全可以写一个永远通过的假测试。这一层的防伪需要代码覆盖率、人工抽查等配套手段。phase-barrier 的定位很明确：**先解决"有没有走流程"，再谈"流程质量"**。

## 使用方式

```bash
pip install phase-barrier
```

最小接入：

```python
from anti_shortcut import AntiShortcutSkill

skill = AntiShortcutSkill(
    workspace=".",
    user_request="实现一个计算斐波那契数列的函数",
)
tools = skill.install(agent_tools)   # 替换 Agent 工具表，注入 advance_stage
```

如果 Agent 试图跳步，会被这样拦下来：

```
[BLOCKED] write_file(fib.py, ...)
          -> 当前阶段不允许编写实现代码：请先完成测试用例编写（阶段 2）...
[BLOCKED] execute_command(pytest -q)
          -> 当前阶段不允许运行测试命令：请先完成实现代码（阶段 3）...
```

仓库里有完整的最小可运行示例：`examples/minimal_agent.py`，一键跑通"跳步被拦 -> 按 SOP 推进到交付"的全过程。

## v0.3.0：语言适配层

早期版本只认识 Python。v0.3.0 把语言相关逻辑抽象成了 `LanguageAdapter` 接口：

- 文件识别（哪些是测试文件、哪些是实现文件）
- 语法检查（`py_compile` / `node --check` / `tsc --noEmit`）
- 测试校验（AST 解析 / 启发式关键字统计）
- 测试命令识别与输出解析

内置 Python、JavaScript/TypeScript 两个适配器；第三方可以通过 `phase_barrier.languages` 入口点注册自己的适配器，也可以直接用 `language_adapter: "my_module.MyAdapter"` 配置导入。选择优先级：显式配置 > 自动检测（`package.json` / `pom.xml` / `go.mod` 等标志文件）> 默认 Python。

## 效果、局限与 Roadmap

**效果**：本地 119 个测试覆盖状态机、拦截器、校验器、CLI；CI 矩阵 Python 3.10–3.14 全绿；发布到 PyPI，版本由 git tag 驱动。

**局限**（如实说）：

- 防跳步有效，防"假测试"需要额外手段（覆盖率、抽查）；
- shell 命令解析是启发式的，极端混淆写法理论上可能绕过——生产环境建议配合 Docker 只读卷 + 沙箱策略；
- 目前内置适配器只有 Python 和 JavaScript/TS，Java/Go/Rust 在规划中。

**Roadmap**：

- v0.4.x：Java 适配器、GitHub Action 门禁、插件机制文档完善
- v0.5.x：Kubernetes sidecar、状态文件签名、sigstore 供应链签名

## 结语

phase-barrier 想回答的问题很简单：**当 Agent 成为"写代码的主力"，谁来保证它不图快？** 答案是：把工程规范变成环境约束，而不是靠 Agent 自觉。

项目地址：[github.com/Xuqing0415/phase-barrier](https://github.com/Xuqing0415/phase-barrier)，PyPI: [phase-barrier](https://pypi.org/project/phase-barrier/)。欢迎试用、提 issue，也欢迎参与 Roadmap 上的功能开发。