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

| 插件 | 类型 | 作者 | 说明 |
|------|------|------|------|
| （待社区提交） | | | 第一个第三方插件等你来 |
