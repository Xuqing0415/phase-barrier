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
