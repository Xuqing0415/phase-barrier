# 反捷径校验 Skill（Anti-Shortcut Validation Skill）

[![CI](https://github.com/Xuqing0415/phase-barrier/actions/workflows/ci.yml/badge.svg)](https://github.com/Xuqing0415/phase-barrier/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/phase-barrier.svg)](https://pypi.org/project/phase-barrier/)
[![Python versions](https://img.shields.io/pypi/pyversions/phase-barrier.svg)](https://pypi.org/project/phase-barrier/)

强制编码 Agent（如 Alpha-SWE）遵循标准工程师 SOP 的**阶段门禁（Stage Gate）**组件：
以“阶段状态机 + 证据校验 + 工具拦截”的组合，阻止 Agent 跳步、偷步或伪造产出。

> 流程：**需求 → spec 设计 → 测试用例 → 实现 → 测试 → 修复 → 交付**

## 特性

- **不可绕过**：校验逻辑位于 Agent 工具调用层，Agent 无法通过自然语言指令绕过；状态文件由 Skill 独占原子写入。
- **最小侵入**：通过包装 `write_file` / `execute_command` 实现拦截，不改变核心工具接口。
- **证据明确**：每个阶段要求具体可验证的产物（文件、AST 统计、测试退出码与摘要）。
- **自动校验**：spec 章节检查、测试 AST 分析（函数数量 + 断言）、实现语法检查、测试结果解析，全部自动完成。
- **可配置**：YAML + Pydantic 配置，可自定义阶段要求、文件模式、测试命令，或关闭某些严格校验。

## 架构

```
Alpha-SWE Agent Core（思考 / 规划 / 调用工具）
        │  工具调用（write_file, execute_command, advance_stage）
        ▼
反捷径校验 Skill（中间件）
        ├── 状态机      ：阶段与证据持久化到 <workspace>/.agent_gate/state.json
        ├── 证据校验    ：每个阶段的校验函数（validators）
        └── 工具拦截    ：包装 write_file / execute_command，注入 advance_stage
        │  合法调用
        ▼
执行环境（文件系统 / Shell）
```

Mermaid 版本（GitHub 上渲染）：

```mermaid
flowchart TD
    A[Agent Core 思考 / 规划 / 调用工具] -->|write_file / execute_command| B{反捷径校验 Skill 中间件}
    B --> C[工具拦截器<br/>check_write_permission<br/>check_exec_permission]
    C -- 违规 --> X[拒绝并返回错误提示<br/>BLOCKED / REJECTED]
    C -- 合法 --> D[执行环境<br/>文件系统 / Shell]
    B --> S[状态机 state.json<br/>当前阶段 / 证据 / 历史]
    B --> V[证据校验 validators<br/>spec / 测试 AST / 语法 / 测试结果]
    S -->|advance_stage| V
    V -- 通过 --> S
```

## 快速开始

```bash
pip install phase-barrier        # 从 PyPI 安装（发行名与仓库同名）
# 或本地构建后安装：
#   python -m pip install --upgrade build
#   python -m build
#   pip install dist/phase_barrier-*.whl

pip install -e .            # 开发模式安装（依赖 pydantic / pyyaml / structlog）
python examples/minimal_agent.py   # 最小可运行的 Agent 接入示例（拦截 + 正常流程）
python examples/demo.py            # 完整演示（含违规尝试被拦截）
python -m pytest                   # 运行测试套件
```

### 集成到 Agent（Alpha-SWE 等基于工具调用的 Agent）

```python
from anti_shortcut import AntiShortcutSkill

# 1. 启动时创建 Skill（user_request 由系统传入，作为阶段 0 证据）
skill = AntiShortcutSkill(
    workspace=".",
    config="anti_shortcut_config.yaml",   # 可选
    user_request="实现一个计算斐波那契数列的函数",
)

# 2. 用包装后的工具替换 Agent 工具表中的原始工具，并注入 advance_stage
tools = skill.install(agent.tools)

# 3. Agent 后续只能调用 tools["write_file"] / tools["execute_command"] / tools["advance_stage"]
```

最小可运行示例见 [`examples/minimal_agent.py`](examples/minimal_agent.py)：一个模拟 Agent 循环
先尝试跳步（被拦截），再按 spec → 测试 → 实现 → 运行测试推进到交付，可直接 `python examples/minimal_agent.py` 运行。

### 一键接入 + 插件加载

```python
from anti_shortcut import bootstrap, register_integration

# 一步完成：创建 Skill -> 包装工具 -> 注入 advance_stage -> 加载插件
bootstrap(
    agent_tools=agent.tools,          # Agent 暴露的工具表
    workspace=".",
    user_request="实现一个计算斐波那契数列的函数",
    agent=agent,                      # 透传给集成插件
)

# 进程内注册集成插件（宿主启动时注册，插件负责把包装后的工具装回 Agent）
def my_installer(agent, skill):
    skill.install(agent.tools)

register_integration("alpha-swe-adapter", my_installer)
```

发布为独立包的插件可声明入口点组 `anti_shortcut.integrations`（`pyproject.toml`）：

```toml
[project.entry-points."anti_shortcut.integrations"]
alpha-swe = "alpha_swe_adapter:install"
```

`load_plugins(agent, skill)` 会自动发现并执行入口点插件。

### 命令行门禁检查（编排器 / 人工监督）

```bash
python -m anti_shortcut inspect --workspace .            # 查看当前阶段
python -m anti_shortcut inspect --workspace . --json     # JSON 输出（便于自动化）
python -m anti_shortcut advance --workspace . --to 2     # 推进阶段（校验证据）
```

`advance` 与 Agent 内部的 `advance_stage` 走同一套证据校验：通过返回退出码 0，被拒绝返回 1 并打印原因。

## 阶段定义与证据要求

| 阶段 | 名称 | 必需证据 | 校验方式 |
|------|------|----------|----------|
| 0 | 需求接收 | 用户需求原文（系统传入） | 自动记录 |
| 1 | Spec 设计 | `spec.md`，含 `## 需求分析` / `## 设计方案` / `## 接口定义`，且足够详细 | 文件存在 + 章节匹配 + 最小长度 |
| 2 | 测试用例 | `test_*.py` 等，测试函数数量 ≥ 阈值，每个函数含断言 | 文件存在 + AST 解析 |
| 3 | 实现代码 | 非测试 `*.py` 源码 | 文件存在 + 语法编译检查 |
| 4 | 运行测试 | 测试命令执行记录（退出码 + 输出摘要） | 拦截器记录 `last_test_run` |
| 5 | 修复与回归 | 修复后的代码 + 重新运行的测试全部通过 | 测试通过且发生在最后一次代码修改之后 |
| 6 | 交付 | （可选）交付总结 | 达到阶段 6 即完成 |

特殊分支：阶段 4 推进时，若最近一次测试**全部通过**且代码未被后续修改，则**跳过阶段 5 直接进入交付**；否则进入阶段 5 修复。

## 工具拦截规则

| 工具 / 命令 | 拦截条件 | 提示 |
|-------------|----------|------|
| `write_file` 写实现代码 | 阶段 < 2（测试未完成） | 请先完成测试用例编写 |
| `write_file` 写测试文件 | 阶段 < 1（spec 未完成） | 请先完成 spec 设计 |
| `write_file` 写 `.agent_gate/` | 任意阶段 | 门禁目录由 Skill 独占 |
| `execute_command` 运行测试（pytest 等） | 阶段 < 3（实现未完成） | 请先完成实现代码 |
| shell 写入源码/测试（`>`、`sed -i`、`mv`、`rm`、`touch`…） | 同上按文件类型 | 与 `write_file` 相同的阶段限制 |
| 任何访问 `.agent_gate` 的命令 | 任意阶段 | 禁止通过 shell 访问门禁目录 |
| 只读命令（`ls` / `cat` / `dir` …） | 无 | 放行 |

`advance_stage(new_stage)` 是唯一合法的阶段推进入口：

- `new_stage` 必须等于当前阶段 + 1，否则返回“不允许跳跃阶段”；
- 推进前运行**当前阶段**的证据校验器，不通过则返回详细失败原因；
- 通过后写入状态机（原子写：临时文件 + `os.replace`），并记录证据哈希。

## 状态与审计

- 状态文件：`<workspace>/.agent_gate/state.json` —— 当前阶段、阶段历史、证据哈希、最近测试结果。
- 审计日志：`<workspace>/.agent_gate/audit.log` —— 结构化 JSON，记录阶段变更、拦截事件、校验结果。

示例状态：

```json
{
  "version": 1,
  "current_stage": 2,
  "completed_stages": [0, 1],
  "stage_history": [
    { "stage": 0, "name": "需求接收", "timestamp": "...", "evidence": {"user_request": "..."} },
    { "stage": 1, "name": "Spec 设计", "timestamp": "...", "evidence": {"spec": {"sha256": "..."}} }
  ],
  "evidence": { "user_request": "...", "spec": {}, "tests": {}, "implementation": {}, "last_test_run": {} }
}
```

## 配置

参考 [`examples/anti_shortcut_config.yaml`](examples/anti_shortcut_config.yaml)（缺省使用内置默认值）：

```yaml
min_test_functions: 2              # 测试函数数量阈值
spec_sections: ["## 需求分析", "## 设计方案", "## 接口定义"]
test_file_patterns: ["test_*.py", "tests/**/test_*.py"]
test_commands: ['^\\s*pytest\\b', '^\\s*npm\\s+test\\b', ...]
protect_gate_dir: true             # 生产环境配合只读卷挂载
allow_other_files_any_stage: true  # 其他文件类型（README 等）是否不限阶段
```

## 安全与防绕过

- **状态文件保护**：Agent 可用的工具全部被包装；`.agent_gate` 的任何写入 / shell 访问都被拦截。
- **生产部署**：将 `.agent_gate` 以只读卷挂载给 Agent 执行容器（`-v /host/path:/.agent_gate:ro`），实现进程级隔离——即使 Agent 绕过工具包装也无法篡改状态。
- **空壳防护**：测试文件经 AST 分析，要求测试函数数量 ≥ 阈值且每个函数含断言（`assert` / `pytest.raises`）。
- **伪造防护**：证据文件在推进时记录 SHA-256；阶段 5 要求测试运行发生在最后一次代码修改之后。
- **日志审计**：所有拦截与阶段变更写入 JSON 审计日志，便于事后分析“哪些请求被拦截”“跳过步骤的频率”。

## Docker 只读卷部署（进程级防绕过）

即使 Agent 绕过工具包装直接操作文件系统，也可通过“只读挂载”从文件系统层面锁死 `.agent_gate`：

```bash
docker compose -f deploy/docker-compose.yml up --build
```

- `gate-keeper` 服务：对 `/workspace/.agent_gate` **可写**，负责初始化状态并跑完整门禁流程；
- `agent` 服务：`/workspace` 可写（产出代码），但 `/workspace/.agent_gate` **只读挂载**（`:ro`）；
- `agent` 侧探针验证：读状态正常、写门禁目录被拒绝（`PermissionError`）、写工作区源码正常。

详见 [`deploy/README.md`](deploy/README.md)。

## 模块结构

```
anti_shortcut/
├── __init__.py        # 公共 API
├── config.py          # GateConfig（Pydantic）+ YAML 加载
├── state.py           # StateManager：JSON 原子持久化、阶段历史、证据
├── validators.py      # 各阶段证据校验器（spec / tests / implementation / test_run / retest）
├── interceptors.py    # 命令分类、门禁目录检测、shell 写路径提取、测试输出摘要
├── audit.py           # 结构化 JSON 审计日志（structlog，按文件独立实例）
├── skill.py           # AntiShortcutSkill：工具包装 + advance_stage + 权限检查
├── integration.py     # 集成层：bootstrap / 插件注册 / 入口点发现
├── paths.py           # 路径工具：glob 匹配 / 文件遍历 / SHA-256
├── languages/         # 语言适配层（v0.3.0）
│   ├── base.py        #   LanguageAdapter 抽象基类 + 共享校验策略
│   ├── python.py      #   PythonAdapter（AST + compile）
│   ├── javascript.py  #   JavaScriptAdapter（node --check / tsc --noEmit + 启发式测试）
│   └── __init__.py    #   注册表 / detect_language / get_adapter / 入口点加载
└── __main__.py        # CLI：python -m anti_shortcut inspect / advance
examples/
├── demo.py                        # 模拟 Agent 完整演示（含违规拦截）
├── minimal_agent.py               # 最小可运行 Agent 接入示例
├── anti_shortcut_config.yaml      # Python 项目示例配置
└── anti_shortcut_js_config.yaml   # JavaScript / TypeScript 项目示例配置
deploy/
├── Dockerfile                     # 打包镜像（含 CLI）
├── docker-compose.yml             # gate-keeper（可写）+ agent（.agent_gate 只读）
├── seed_gate.py                   # gate-keeper：初始化并跑完整门禁流程
├── probe.py                       # agent 探针：验证只读挂载生效
└── README.md                      # 部署说明
tests/                             # pytest 测试套件（117 个用例）
```

## 设计取舍

- **阶段 4 → 6 跳过修复**：测试一次通过时不必强制走修复阶段（见第 7 章工作流）。
- **修复后强制回归**：阶段 5 校验最近一次测试必须“通过”且“晚于最后一次代码修改”，防止改完不重测。
- **启发式 shell 解析**：`sed -i`、重定向等写路径提取是尽力而为；核心强制边界是工具包装 + 只读挂载，shell 解析用于纵深防御。
- **测试质量**：本 Skill 防“跳步”，不负责“测试写得好不好”；覆盖率与人工抽查可作为补充（见第 10 章）。

## 环境说明

- 实现语言：Python 3.10+（已在 3.14 验证）
- 依赖：`pydantic>=2`、`PyYAML>=6`、`structlog>=23`（可选 `pytest` 用于测试）
- 跨平台：Windows / Linux / macOS（门禁目录权限建议在 Linux 容器 + 只读卷场景使用）

## 多语言支持（v0.3.0 语言适配层）

v0.3.0 起，语言相关逻辑（文件识别、语法检查、测试统计、测试命令识别）抽象为
**语言适配器（Language Adapter）**。核心包内置 Python 与 JavaScript/TypeScript 适配器，
第三方可注册自定义适配器；未显式指定时按工作区标志文件自动检测。

### 快速启用

```python
from anti_shortcut import AntiShortcutSkill

# 显式指定语言（优先级最高），无需再手工配文件模式
skill = AntiShortcutSkill(
    workspace=".",
    config={"language": "javascript"},
    user_request="实现一个计算斐波那契数列的函数",
)
```

```yaml
# 或 YAML（完整示例见 examples/anti_shortcut_js_config.yaml）
language: javascript
min_test_functions: 2
test_commands:
  - '^\s*npm\s+test\b'
  - '^\s*npx\s+(jest|vitest|mocha|playwright)\b'
  - '^\s*npx\s+tsc\s+--noEmit\b'
```

不写 `language` 时自动检测标志文件：`package.json` → `javascript`，`pom.xml` → `java`，
`go.mod` → `go`，`Cargo.toml` → `rust`，`requirements.txt` / `setup.py` / `pyproject.toml` → `python`；
未识别时默认 Python。适配器默认文件模式与 YAML 中的 `test_file_patterns` / `source_file_patterns`
自动合并（配置只增不减）。

### 内置适配器

| 适配器 | 文件识别 | 语法检查 | 测试校验 |
|--------|----------|----------|----------|
| `PythonAdapter` | `test_*.py` / `tests/**` 为测试，`*.py` 为实现 | `compile()` | AST 解析：测试函数数 + `assert` / `pytest.raises` |
| `JavaScriptAdapter` | `*.test.js` / `*.spec.ts` / `__tests__/` 为测试，`src/**` 与 `*.js|ts|jsx|tsx` 为实现 | `node --check` / `tsc --noEmit`（工具缺失时返回明确错误） | 轻量启发式：`test` / `it` / `describe` 声明数 + `expect` / `assert` / `.toBe` 等断言关键字 |

### 自定义适配器

只需 4 步即可接入一种新语言（10 分钟内可完成最小实现）：

**1. 实现 `LanguageAdapter`**（文件识别用默认模式即可，至少实现 `check_syntax`）：

```python
# my_adapters.py
from anti_shortcut.languages import LanguageAdapter

class MyLanguageAdapter(LanguageAdapter):
    name = "mylang"
    source_file_patterns = ["*.foo"]        # 实现文件模式
    test_file_patterns = ["*.test.foo"]     # 测试文件模式
    test_command_patterns = [r"^\s*foo\s+test\b"]  # 测试命令正则

    def check_syntax(self, path):
        return True, "ok"   # 返回 (是否通过, 错误信息)

    def analyze_tests(self, path):
        # 返回 {"test_functions": [...], "assertions_total": N}
        # 可参考 anti_shortcut/languages/javascript.py 的启发式实现
        ...
```

**2. 本地配置加载**（无需打包，直接指定导入路径）：

```yaml
language_adapter: "my_adapters.MyLanguageAdapter"
adapter_options:
  min_test_functions: 3    # 传给适配器的额外参数（由适配器自行解释）
```

**3. 打包发布为独立包**（便于复用与分享）：

```toml
[project]
name = "phase-barrier-mylang-adapter"
version = "0.1.0"
dependencies = ["phase-barrier>=0.3.0"]

[project.entry-points."phase_barrier.languages"]
mylang = "my_adapters:MyLanguageAdapter"
```

```bash
python -m build && twine check dist/* && twine upload dist/*
```

**4. 入口点注册后按名称引用**（安装插件包即可，无需再写导入路径）：

```yaml
language: mylang
```

**可运行示例**：`examples/custom_adapter/` 提供了一个虚构 `.foo` 语言的完整插件
（`foo_language.py` + `foo_config.yaml` + `pyproject.toml`），运行
`python examples/custom_adapter/demo.py` 可看到自定义适配器参与
文件识别、语法检查、测试校验与测试命令识别的完整拦截流程。

适配器选择优先级：显式 `language` > 自定义 `language_adapter` > 自动检测 > 默认 Python。

## 常见问题（FAQ）

**如何自定义阶段或证据要求？**
通过 YAML 配置覆盖即可，无需改代码：

```yaml
spec_file: design.md
spec_sections: ["## 需求", "## 方案", "## 接口"]
spec_min_chars: 80
min_test_functions: 3
```

把配置路径传给 `AntiShortcutSkill(..., config="my_gate.yaml")` 或 CLI 的 `--config`。

**如何关闭某道门禁？**
每个校验器都有开关或阈值可调，例如：

- 不强制实现代码：`require_implementation: false`
- 允许任意阶段写“其他”文件：`allow_other_files_any_stage: true`（默认已开启）
- 调低 spec 长度门槛：`spec_min_chars: 0`
- 关闭门禁目录 shell 保护（不推荐）：`protect_gate_dir: false`

彻底“一键关闭全部门禁”与设计目标相悖，不支持。

**如何适配非 Python 项目？**
v0.3.0 起推荐使用语言适配层：`language: javascript`（内置 Python / JavaScript 适配器，
并支持按工作区标志文件自动检测）；更特殊的语言可提供自定义 `LanguageAdapter`
（用 `language_adapter` 配置导入路径）。不引入适配器时，仍可直接配置
`test_file_patterns` / `source_file_patterns` / `test_commands` 三项，
门禁逻辑（阶段状态机 + 证据校验 + 工具拦截）保持不变。

**Agent 被拦截后如何继续？**
拦截只返回错误提示，不破坏任何状态。Agent 补齐当前阶段证据（如写完 `spec.md`）后重新调用
`advance_stage` 即可；也可以由编排器用 CLI `python -m anti_shortcut advance --workspace . --to N` 人工复核后推进。

## Roadmap（规划）

- **v0.4.0 更多语言适配器**：在 v0.3.0 语言适配层基础上接入 Java（`javac`）、Go、Rust 等；JavaScript 测试校验从启发式升级为真实解析（acorn / `jest --listTests`）。
- **插件机制增强**：通过入口点注册自定义校验器与拦截规则（当前已有集成插件入口点 `anti_shortcut.integrations`）。
- **CI 门禁集成**：提供 GitHub Action，让 phase-barrier 直接在 CI 中作为阶段闸门使用。
- **Kubernetes sidecar**：以 sidecar 容器承载门禁状态，与主 Agent 容器共享工作区、状态目录只读挂载。
- **安全增强**：状态文件与证据签名（不可信环境防篡改）；审计日志远程推送（SIEM）。
- **供应链**：接入 sigstore 签名与 trusted publishing，提升包可信度。

版本按 tag 驱动发布（`git tag vX.Y.Z && git push origin vX.Y.Z`），每次发版更新 CHANGELOG。

## 反馈与贡献

- 使用中遇到问题或想提需求：请在 [GitHub Issues](https://github.com/Xuqing0415/phase-barrier/issues) 反馈，最好附上复现步骤（版本、配置、命令输出）。
- 关注 PyPI 下载量与版本更新：[phase-barrier · PyPI](https://pypi.org/project/phase-barrier/)。
- 欢迎贡献代码：提交前请运行 `python -m pytest`，并遵循 Conventional Commits 提交规范（`feat:` / `fix:` / `docs:` / `test:`）。

## 构建与发布（PyPI）

构建并检查发行包：

```bash
python -m pip install --upgrade build twine
python -m build          # 生成 dist/*.tar.gz 与 dist/*.whl
twine check dist/*       # 校验元数据与 README 渲染
```

发布（需在 PyPI 注册账号，并配置 `~/.pypirc` 或 `TWINE_*` 环境变量）：

```bash
twine upload dist/*      # 正式发布到 PyPI
# twine upload --repository testpypi dist/*   # 先发 TestPyPI 验证
```

- 版本号由 git tag 驱动（`setuptools-scm`）：打 `vX.Y.Z` tag 后构建即为 `X.Y.Z`，无需再手工同步 `pyproject.toml` 与 `__init__.py`。发布流程：`git tag v0.1.1 && git push --tags`。
- CI（`.github/workflows/ci.yml`）：push / PR 时在 Python 3.10–3.14 矩阵上运行 `pytest` + `examples/demo.py`；`package` job 构建 sdist/wheel 并执行 `twine check` 后上传为 artifact。
- 自动发布（`.github/workflows/release.yml`）：打 `v*` tag 时自动构建并发布到 PyPI，使用仓库 Secret `PYPI_API_TOKEN`。
- 发行名说明：本项目发行名为 `phase-barrier`（与仓库同名），import 包名仍为 `anti_shortcut`，CLI 命令仍为 `anti-shortcut`。
