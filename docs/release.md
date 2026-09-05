# 构建发布与供应链安全

> 迁移自 README 精简版主页；GitHub Action 上架流程见 [publish-to-marketplace](publish-to-marketplace.md)。[返回 README](https://github.com/Xuqing0415/phase-barrier#readme)

## 发布节奏与版本语义（v0.48.3 起执行）

版本号是**里程碑**，不是提交计数。日常改动累积到 main，只有形成用户可感知的
里程碑或紧急修复时才打 tag 发布；**禁止同一天发布多个版本**。

- 累积制：功能新增、缺陷修复、文档与测试改进先在 main 连续提交，不打 tag。
- 发版条件（全部满足才打 tag）：
  1. 至少一项用户可感知变更（新功能 / 行为变化 / 紧急修复）；
  2. CI 全绿且覆盖率 ≥90%；
  3. CHANGELOG 已把累积变化汇总为一条版本条目（Keep a Changelog 风格，只写用户
     可见变化；内部重构 / 测试 / 文档微调不单独成条目，可在条目末尾一句话带过）；
  4. 距上次发布已有沉淀（同日多次发布视为流程违规，紧急安全修复除外）。
- 版本语义（0.x 阶段，从简）：`X.Y` = 用户可感知功能里程碑；`X.Y.Z` = 紧急修复或
  上一版本缺陷（克制使用）；文档 / 测试 / 内部改动**不升版本**。
- 中间态分发：确需正式版前让用户验证时用预发布 tag（如 `v0.49.0rc1`），PyPI
  默认不将 pre-release 装给普通用户，稳定后再发正式版。
- 真实场景验证优先：发布前把可执行的真实场景验证（Docker / kind e2e / 真实工具链
  用例 / 上游集成）跑通；不做纯"刷版本"式发布。

发布流程（`git tag vX.Y.Z` 触发）会用 sigstore 以 GitHub OIDC 身份对 sdist / wheel
签名，`.sigstore.json` 签名随 GitHub Release 附件发布。用户可离线校验包来源：

```bash
# 需要 cosign（https://docs.sigstore.dev/cosign/）
cosign verify-blob --signature phase_barrier-0.10.0-py3-none-any.whl.sigstore.json   --certificate-identity-regexp 'https://github.com/Xuqing0415/phase-barrier/.github/workflows/release.yml@refs/tags/v'   --certificate-oidc-issuer https://token.actions.githubusercontent.com   phase_barrier-0.10.0-py3-none-any.whl
```

信任根：GitHub OIDC issuer（`https://token.actions.githubusercontent.com`）+ 工作流身份，
确保包确实由本仓库的 release 工作流构建并发布。

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
- CI（`.github/workflows/ci.yml`）：push / PR 时在 Python 3.10–3.14 矩阵上运行 `pytest` + `examples/demo.py` + `examples/orchestrator_hooks/multi_agent.py`（多 Agent 并发）；矩阵安装 Node.js / Go / Rust / Ruby 真实工具链，激活 JS/Go/Rust/Ruby 适配器真实工具测试；`coverage` job 运行 `coverage run -m pytest` + `coverage report --fail-under=90`（核心包 ≥90%）并上传 `coverage.json`；`package` job 构建 sdist/wheel 并执行 `twine check` 后上传为 artifact。
- 自动发布（`.github/workflows/release.yml`）：打 `v*` tag 时自动构建并发布到 PyPI，使用 **Trusted Publishing（OIDC）**，无需仓库 Secret。首次使用需在 [PyPI 项目设置](https://pypi.org/manage/project/phase-barrier/settings/publishing/) 添加 Trusted Publisher：Provider `GitHub`、Owner `Xuqing0415`、Repository `phase-barrier`、Workflow name `release.yml`；发布时会同时生成 PyPI 侧 PEP 740 attestations。
- 发行名说明：本项目发行名为 `phase-barrier`（与仓库同名），import 包名仍为 `anti_shortcut`，CLI 命令仍为 `anti-shortcut`。
