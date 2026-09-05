# 插件与生态索引（v0.26.0）

phase-barrier 通过 Python 入口点（entry points）支持四类插件：

| 入口点组 | 作用 | 参考实现 |
|----------|------|----------|
| `phase_barrier.languages` | 自定义语言适配器 | `examples/custom_adapter/` |
| `phase_barrier.validators` | 自定义阶段校验器（覆盖内置） | `examples/plugin_rules/` |
| `phase_barrier.interceptors` | 自定义拦截规则 | `examples/plugin_rules/` |
| `anti_shortcut.integrations` | Agent 集成插件（自动装回包装后的工具） | `examples/orchestrator_hooks/` |

> **自动收录（v0.46.0）**：索引数据由 `plugins.json` 承载、每周自动验证工作流维护，
> 已收录插件的实时状态表见[插件状态页](plugin-status.md)。若希望你的插件被自动收录，
> 请给仓库添加 `phase-barrier-plugin` 主题，并确保可通过
> `python -m anti_shortcut plugin-verify` 验证（入口点全部通过）；详见下文
> 「提交到索引 / 自动收录」与「自动发现脚本」。

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

## 提交到索引 / 自动收录

第三方插件推荐走**自动发现**（v0.46.0）：不再依赖人工合并，满足条件即被每周
workflow 自动收录：

1. 用官方模板仓库 [phase-barrier-plugin-template](https://github.com/Xuqing0415/phase-barrier-plugin-template) 生成插件仓库（Use this template）。
2. 给仓库添加 GitHub topic：`phase-barrier-plugin`。
3. 接入上面的 **插件 CI 模板**，保证 `python -m anti_shortcut plugin-verify` 全绿
   （入口点可加载、类型与必需接口通过）。
4. 等待周期自动发现（每周一 03:00 UTC，或提 `workflow_dispatch` 手动触发）；
   验证通过的插件以 `auto_discovered: true` 自动进入 `plugins.json`，并同步到「插件状态页」（见文末链接）。
   自动收录只校验入口点可用性，不审查代码质量（质量由社区 / Issue 反馈约束）。

不打 topic 的插件仍可走人工提交：

1. 在 [GitHub Issues](https://github.com/Xuqing0415/phase-barrier/issues) 选择
   「插件提交（Plugin Submission）」模板，附上：包名 / 仓库链接、支持的入口点组、
   CI 状态链接、一段使用示例。
2. 维护者审核后合并到 `plugins.json`，并运行
   `python scripts/verify_plugins.py --update --sync-docs` 刷新插件状态页。

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
| （等待自动发现 / 人工提交） | | | 打 `phase-barrier-plugin` 主题或提 Issue，见「提交到索引 / 自动收录」 |

## 索引自动检查（v0.34.1）

主仓库新增 `.github/workflows/plugin-check.yml`：每周一 06:00 UTC 自动运行
（也可手动 `workflow_dispatch` 触发），安装 `examples/custom_adapter` 与
`examples/plugin_rules` 两个官方示例插件后执行 `python -m anti_shortcut plugin-verify --json`，
断言 `foo`（语言适配器）/ `strict_design`（校验器）/ `deny_vendor`（拦截规则）等入口点全部通过，
并把 JSON 报告作为 artifact（`plugin-check-report.json`）上传供人工抽查。

说明：

- 该检查针对**本仓库官方示例插件**，防止示例随代码演进失效。
- **第三方插件** 的收录（v0.46.0 起）优先走自动发现：GitHub topic
  `phase-barrier-plugin` + 插件 CI 全绿 -> `plugin-verification.yml`（每周一
  03:00 UTC）自动 clone / 安装 / `plugin-verify`，通过即以
  `auto_discovered: true` 写入 `plugins.json` 并同步插件状态页；不打 topic 的仍走
  Issue 模板人工提交。

## 索引数据文件与自动验证（v0.45.0 / v0.46.0）

从 v0.45.0 起，索引不只靠文档表格人工维护，还新增了机器可读的
`plugins.json`（仓库根目录）与自动验证脚本，方便维护者周期核查已收录插件的
兼容性；v0.46.0 起顶层改为容器结构并支持按 GitHub topic 自动发现新插件：

```json
{
  "plugins": [
    {
      "name": "owner/repo",
      "repo": "https://github.com/owner/repo",
      "install": "git+https://github.com/owner/repo.git#egg=repo",
      "entry_points": { "phase_barrier.languages": ["my_language"] },
      "last_verified": "2026-09-05T00:00:00Z",
      "status": "passed",
      "auto_discovered": true,
      "last_commit_sha": "abc123..."
    }
  ],
  "auto_discovery": { "github_topic": "phase-barrier-plugin", "enabled": true }
}
```

- `plugins`：条目列表（v0.45.x 的顶层数组旧格式仍可读取，写回时自动升级为容器）。
- `auto_discovery`：自动发现开关与使用的 GitHub topic（置 `enabled: false` 可停用）。
- `name`：插件标识（官方示例用包名，自动收录的第三方用 `owner/repo`）。
- `repo`：仓库地址（本地示例用相对路径，第三方用 URL）。
- `install`：可被 `pip install -e` 的目标（本地相对路径 / `git+https` URL）；
  缺省回退到 `repo`。无 `install` / `repo` 的占位条目不会自动判定状态。
- `entry_points`：声明该插件应提供的入口点，`{入口点组: [名称列表]}`；
  也可用 `["phase_barrier.languages", ...]` 简写（只断言组内有可用入口点）。
- `last_verified` / `status`：最近一次自动验证时间与结果（`passed` /
  `failed` / `unverified`），由验证脚本按 `--update` 写回。
- `auto_discovered` / `last_commit_sha`：是否由自动发现收录 / 收录时验证的
  提交 SHA（用于后续增量判断），官方示例为 `false` / `null`。

### 验证脚本

`scripts/verify_plugins.py` 负责“索引层”验证：逐个安装索引条目（新进程注册
入口点），再在全新子进程中运行 `python -m anti_shortcut plugin-verify --json`，
断言每个条目声明的入口点全部可用：

```bash
python scripts/verify_plugins.py                  # 验证并打印摘要（0 = 全通过）
python scripts/verify_plugins.py --update         # 把 status / last_verified 写回
python scripts/verify_plugins.py --update --sync-docs  # 写回状态并同步 docs/plugin-status.md 插件状态页
python scripts/verify_plugins.py --json           # 结构化报告（stdout）
python scripts/verify_plugins.py --no-install     # 跳过安装（插件须已安装）
```

### 自动发现脚本（auto_discover_plugins.py，v0.46.0）

`scripts/auto_discover_plugins.py` 负责“发现并收录”：按 GitHub topic 搜索
（Search API，token 取 `GH_TOKEN` / `GITHUB_TOKEN` / `--token`），过滤已在
`plugins.json` 中的仓库；对候选执行 `git clone --depth 1` -> `pip install -e`
-> `plugin-verify --json` 全链路验证，通过则以 `auto_discovered: true` +
`last_commit_sha` 收录并同步插件状态页；失败只记录原因、不收录（不审查代码）。
v0.47.0 起，已收录的自动条目还会用 `git ls-remote` 检测远端 HEAD，与
`last_commit_sha` 不一致时增量重新验证并更新入口点（含新增入口点）：

```bash
python scripts/auto_discover_plugins.py               # dry-run：搜索并打印候选
python scripts/auto_discover_plugins.py --update      # 验证候选 / 刷新自动条目并写回 + 同步状态页
python scripts/auto_discover_plugins.py --update --json   # 结构化摘要（stdout）
```

### 周期自动验证（plugin-verification.yml）

`.github/workflows/plugin-verification.yml` 每周一 03:00 UTC（可与周一 06:00 的
`plugin-check.yml` 区分）自动运行两步：

1. `auto_discover_plugins.py --update`：按 topic 自动发现并收录新第三方插件，并
   对已收录自动条目的新提交做增量刷新；
2. `verify_plugins.py --update --sync-docs`：全量安装并验证 `plugins.json` 全部
   条目（含新收录），刷新状态与插件状态页；

随后上传 JSON 报告 artifact（`plugin-verification-report.json` /
`plugin-discovery-report.json`），有变更自动提交到 main。验证失败不会静默：
失败状态写回 `plugins.json` 并随提交 / 报告暴露给维护者处置。

`plugin-check.yml` 继续保留，负责官方示例插件的固定断言；`plugins.json` 驱动的
验证 + topic 自动发现是它的推广形态，第三方插件无需人工合并即可被周期收录
（自动收录只校验入口点可用性）。
> **成为第一个第三方插件（v0.48.0）**：目前索引中的自动收录条目为官方
> 模板仓库；给真实插件仓库打上 `phase-barrier-plugin` topic 并通过插件 CI，
> 即可成为第一个由社区贡献、每周自动验证的第三方条目（流程见上文「提交到
> 索引 / 自动收录」）。
>
> 仓库内置了不依赖真实第三方的端到端演练：`tests/fixtures/plugin_alpha/`
> （模拟插件）+ `tests/test_auto_discover_e2e.py`（真实 git 仓库下验证
> 发现 / 增量刷新 / 失败重试）。

## 插件状态页（自动同步）

已收录插件（官方示例 + 自动发现第三方）的实时状态表见
[插件状态页](plugin-status.md)：由 `scripts/verify_plugins.py --sync-docs`
从 `plugins.json` 自动渲染，随周期 workflow 提交更新，无需人工维护。
