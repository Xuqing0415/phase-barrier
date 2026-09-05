# 插件与生态索引（v0.26.0）

phase-barrier 通过 Python 入口点（entry points）支持四类插件：

| 入口点组 | 作用 | 参考实现 |
|----------|------|----------|
| `phase_barrier.languages` | 自定义语言适配器 | `examples/custom_adapter/` |
| `phase_barrier.validators` | 自定义阶段校验器（覆盖内置） | `examples/plugin_rules/` |
| `phase_barrier.interceptors` | 自定义拦截规则 | `examples/plugin_rules/` |
| `anti_shortcut.integrations` | Agent 集成插件（自动装回包装后的工具） | `examples/orchestrator_hooks/` |

## 开发模板

> **推荐：官方模板仓库**
> 独立模板仓库 [phase-barrier-plugin-template](https://github.com/Xuqing0415/phase-barrier-plugin-template)
> 点 GitHub 右上角 **Use this template** 一键生成插件仓库，内置四类入口点示例、
> CI（`plugin-verify` + pytest）与冒烟测试；下文片段为最小示例，完整模板以该仓库为准。

最小语言适配器插件：

```toml
# pyproject.toml
[project.entry-points."phase_barrier.languages"]
my_language = "my_package:MyLanguageAdapter"
```

```python
# my_package/__init__.py
from anti_shortcut import LanguageAdapter


class MyLanguageAdapter(LanguageAdapter):
    name = "my_language"
    file_extensions = [".xyz"]
    source_file_patterns = ["*.xyz"]
    test_file_patterns = ["test_*.xyz"]

    def check_syntax(self, path):
        return True, "语法检查通过（示例插件）"
```

安装插件包后，用 `language: my_language` 或 `language_adapter: "my_package:MyLanguageAdapter"` 启用。

自定义拦截规则插件：

```toml
[project.entry-points."phase_barrier.interceptors"]
my_rules = "my_package:rules"
```

```python
# my_package/__init__.py
def deny_uploads(kind, target, config, stage, content=None):
    if kind == "write" and "uploads/" in str(target).replace("\\", "/"):
        return False, "禁止写入 uploads/（我的规则）"
    return None

rules = [deny_uploads]
```

## 自动验证（v0.29.0）

安装插件包后，`python -m anti_shortcut plugin-verify` 会自动加载并冒烟验证
全部四类入口点：语言适配器（`name` + 必需方法）、校验器（映射 / 工厂）、
拦截规则（可调用规则）、集成插件（可调用 / `install()`）。返回码 0 = 全部通过。

```bash
pip install phase-barrier my-plugin
python -m anti_shortcut plugin-verify          # 全部通过时退出码 0
python -m anti_shortcut plugin-verify --json   # 结构化结果（CI 断言用）
```

### 插件 CI 模板（一键接入）

把下面工作流复制到插件仓库 `.github/workflows/ci.yml`，每次 push / PR 自动验证：

```yaml
name: Plugin CI

on:
  push:
    branches: [main, master]
  pull_request:

jobs:
  plugin-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: Xuqing0415/phase-barrier/.github/actions/plugin-test@v0.32.2
        with:
          plugin-path: .            # 插件包目录（包含 pyproject.toml）
          python-version: '3.12'
          phase-barrier-version: '' # 留空 = 最新发布版；可固定如 0.29.0
```

验证通过后可在 README 添加徽章：

```markdown
[![Plugin CI](https://github.com/<owner>/<repo>/actions/workflows/ci.yml/badge.svg)](https://github.com/<owner>/<repo>/actions/workflows/ci.yml)
```

## 提交到索引

1. 用官方模板仓库 [phase-barrier-plugin-template](https://github.com/Xuqing0415/phase-barrier-plugin-template) 生成插件仓库（Use this template），把插件发布为公开的 PyPI 包（或提供可安装的 GitHub 仓库）。
2. 在插件仓库接入上面的 **插件 CI 模板**，保证 `plugin-verify` 全绿。
3. 在 [GitHub Issues](https://github.com/Xuqing0415/phase-barrier/issues) 选择
   「插件提交（Plugin Submission）」模板，附上：包名 / 仓库链接、支持的入口点组、
   CI 状态链接、一段使用示例。
4. 维护者审核后合并到下表。

## 官方模板仓库（非插件，用于生成插件）

- [phase-barrier-plugin-template](https://github.com/Xuqing0415/phase-barrier-plugin-template)：
  独立模板仓库，Use this template 即可生成；含四类入口点示例（.demo 语言适配器 / 阶段 1 校验器 /
  vendor 拦截规则 / 集成插件）、`examples/demo.py` 端到端演示、CI（plugin-verify + pytest）。

## 第一批索引（官方示例，v0.26.2）

以下为本仓库自带的官方示例插件，均可直接复用 / 作为提交模板：

| 插件 | 类型 | 作者 | 说明 |
|------|------|------|------|
| `phase-barrier-foo-adapter`（`examples/custom_adapter/`） | `phase_barrier.languages` | phase-barrier 官方示例 | 虚构 `.foo` 语言适配器：文件识别 / 语法检查 / 测试统计 / 测试命令识别；演示打包与入口点注册，安装后 `language: foo` 直接启用 |
| `phase-barrier-plugin-example`（`examples/plugin_rules/`） | `phase_barrier.validators` + `phase_barrier.interceptors` | phase-barrier 官方示例 | 自定义校验器（`strict_design`）+ 拦截规则（`deny_vendor_writes`），安装后自动参与门禁 |
| 编排器钩子集成（`examples/orchestrator_hooks/`） | `anti_shortcut.integrations` | phase-barrier 官方示例 | 任务启动 / 阶段切换钩子调用 `PhaseBarrier`，把包装后的工具装回 Agent |

## 社区第三方插件

| 插件 | 类型 | 作者 | 说明 |
|------|------|------|------|
| （待社区提交） | | | 第一个第三方插件等你来 |

## 索引自动检查（v0.34.1）

主仓库新增 `.github/workflows/plugin-check.yml`：每周一 06:00 UTC 自动运行
（也可手动 `workflow_dispatch` 触发），安装 `examples/custom_adapter` 与
`examples/plugin_rules` 两个官方示例插件后执行 `python -m anti_shortcut plugin-verify --json`，
断言 `foo`（语言适配器）/ `strict_design`（校验器）/ `deny_vendor`（拦截规则）等入口点全部通过，
并把 JSON 报告作为 artifact（`plugin-check-report.json`）上传供人工抽查。

说明：

- 该检查针对**本仓库官方示例插件**，防止示例随代码演进失效。
- **第三方插件** 的收录仍走 Issue 模板 + 插件仓库自带 Plugin CI（见上文模板），状态由作者仓库徽章体现；
  自动轮询第三方插件仓库（`phase-barrier-plugin-index`）属于后续规划，尚未实现。
## 索引数据文件与自动验证（v0.45.0）

从 v0.45.0 起，索引不只靠文档表格人工维护，还新增了机器可读的
`plugins.json`（仓库根目录）与自动验证脚本，方便维护者周期核查已收录插件的
兼容性：

```json
[
  {
    "name": "phase-barrier-foo-adapter",
    "repo": "./examples/custom_adapter",
    "install": "./examples/custom_adapter",
    "entry_points": { "phase_barrier.languages": ["foo"] },
    "last_verified": "2026-09-05T00:00:00Z",
    "status": "passed"
  }
]
```

- `name`：插件包名；`repo`：仓库地址（本地示例用相对路径，第三方用 URL）。
- `install`：可被 `pip install -e` 的目标（本地相对路径 / git+https URL）；
  缺省回退到 `repo`。无 `install` / `repo` 的占位条目不会自动判定状态。
- `entry_points`：声明该插件应提供的入口点，`{入口点组: [名称列表]}`；
  也可用 `["phase_barrier.languages", ...]` 简写（只断言组内有可用入口点）。
- `last_verified` / `status`：最近一次自动验证时间与结果（`passed` /
  `failed` / `unverified`），由验证脚本按 `--update` 写回。

### 验证脚本

`scripts/verify_plugins.py` 负责“索引层”验证：逐个安装索引条目（新进程注册
入口点），再在全新子进程中运行 `python -m anti_shortcut plugin-verify --json`，
断言每个条目声明的入口点全部可用：

```bash
python scripts/verify_plugins.py                  # 验证并打印摘要（0 = 全通过）
python scripts/verify_plugins.py --update         # 把 status / last_verified 写回
python scripts/verify_plugins.py --update --sync-docs  # 写回状态并同步 docs/plugins.md 索引表
python scripts/verify_plugins.py --json           # 结构化报告（stdout）
python scripts/verify_plugins.py --no-install     # 跳过安装（插件须已安装）
```

### 周期自动验证（plugin-verification.yml）

`.github/workflows/plugin-verification.yml` 每周二 05:00 UTC（可与周一 06:00 的
`plugin-check.yml` 区分）自动运行：安装官方索引插件 -> `verify_plugins.py --update --sync-docs`（同步 `plugins.json` 与文档索引表）
-> 上传 JSON 报告 artifact（`plugin-verification-report.json`）-> 若有变更自动
提交到 main。验证失败不会静默：失败状态写回 `plugins.json` 并随提交 / 报告
暴露给维护者处置。

`plugin-check.yml` 继续保留，负责官方示例插件的固定断言；`plugins.json` 驱动的
验证是它的推广形态，第三方插件若提供 `install` 目标并通过维护者审核，即可加入
`plugins.json` 纳入周期自动核查。
### 当前索引状态（由 scripts/verify_plugins.py --sync-docs 自动同步）

运行 `python scripts/verify_plugins.py --sync-docs`（或周期 workflow）后，
下表由 `plugins.json` 自动生成并随提交更新；手动修改会被下一次同步覆盖。

<!-- plugins-index:start -->
| 插件 | 来源 | 入口点 | 状态 | 最近验证 |
|------|------|--------|------|----------|
| phase-barrier-foo-adapter | `./examples/custom_adapter` | languages: foo | passed | 2026-09-05T04:40:30Z |
| phase-barrier-plugin-example | `./examples/plugin_rules` | validators: strict_design; interceptors: deny_vendor | passed | 2026-09-05T04:40:30Z |
<!-- plugins-index:end -->
