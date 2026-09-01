# 插件与生态索引（v0.26.0）

phase-barrier 通过 Python 入口点（entry points）支持四类插件：

| 入口点组 | 作用 | 参考实现 |
|----------|------|----------|
| `phase_barrier.languages` | 自定义语言适配器 | `examples/custom_adapter/` |
| `phase_barrier.validators` | 自定义阶段校验器（覆盖内置） | `examples/plugin_rules/` |
| `phase_barrier.interceptors` | 自定义拦截规则 | `examples/plugin_rules/` |
| `anti_shortcut.integrations` | Agent 集成插件（自动装回包装后的工具） | `examples/orchestrator_hooks/` |

## 开发模板

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

## 提交到索引

1. 把插件发布为公开的 PyPI 包（或提供可安装的 GitHub 仓库）。
2. 在 [GitHub Issues](https://github.com/Xuqing0415/phase-barrier/issues) 选择
   「插件提交（Plugin Submission）」模板，附上：包名 / 仓库链接、支持的入口点组、
   一段使用示例。
3. 维护者审核后合并到下表。

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
