# 贡献指南（Contributing）

感谢你愿意参与 phase-barrier。本指南帮助你快速上手开发、测试与发布。

## 项目架构

```
anti_shortcut/
├── skill.py            # 核心：AntiShortcutSkill（阶段状态机 + 工具拦截 + 证据校验）
├── state.py            # 状态机持久化（.agent_gate/state.json，原子写入 + HMAC 签名）
├── config.py           # Pydantic 配置模型 + YAML 加载（GateConfig）
├── validators.py       # 阶段证据校验器（spec / 测试 / 实现 / 测试运行）
├── interceptors.py     # 工具拦截辅助 + 自定义拦截规则注册
├── rules/              # 内置安全规则包（no_path_traversal 等，v0.26.0）
├── languages/          # 语言适配层（base.py + python/js/java/go/rust/ruby/csharp/dotnet/cpp）
├── sdk.py              # PhaseBarrier 轻量 SDK（编排器钩子）
├── evidence.py         # 证据签名清单（evidence_manifest.json）
├── audit.py            # 审计日志（本地 + 远程推送）
├── sidecar.py / proxy.py / proxy_client.py  # K8s sidecar 透明代理
└── __main__.py         # CLI（inspect / advance / check / verify-evidence / init 等）
```

## 本地开发环境

```bash
python -m pip install -e ".[dev]"
python -m pytest                  # 全量测试
python -m flake8 --jobs=1 <files> # 风格检查（Windows 下需 --jobs=1）
```

测试用 `tmp_path` 隔离工作区；运行完整测试套件约需 3-4 分钟
（含 Java / Ruby 等真实工具用例，环境缺失时自动 skip，CI 中全部激活）。

## 如何添加新语言适配器

1. 阅读 `anti_shortcut/languages/base.py` 的 `LanguageAdapter` 接口。
2. 新建 `anti_shortcut/languages/<lang>.py`，实现：
   - `name` / `file_extensions` / `source_file_patterns` / `test_file_patterns`
   - `check_syntax(path) -> (bool, str)`
   - `analyze_tests(path) -> dict`（测试数量 + 断言统计）
   - `test_command_patterns` 与 `parse_test_output(output, exit_code)`
3. 在 `anti_shortcut/languages/__init__.py` 注册并加入自动检测标志。
4. 在 `pyproject.toml` 的 `phase_barrier.languages` 入口点注册。
5. 新建 `tests/test_<lang>_adapter.py`，覆盖：注册 / 检测 / 文件识别 /
   analyze_tests / parse_test_output（真实工具用例用 `pytest.mark.skipif` 或
   检测工具存在性跳过）。
6. 更新 `docs/configuration.md` 的语言列表与 `examples/` 配置示例。

## 如何添加自定义拦截规则

1. 规则签名：`rule(kind, target, config, stage, content=None) -> (bool, str) | None`。
2. 进程内注册：`from anti_shortcut import register_rule; register_rule("name", rule)`。
3. 发布为插件：入口点组 `phase_barrier.interceptors`（参考 `docs/plugins.md`）。
4. 内置规则包新增规则时，同时更新 `anti_shortcut/rules/__init__.py` 的
   `BUILTIN_RULES` / `RULE_DESCRIPTIONS` 与对应测试。

## 代码风格与测试要求

- 遵循 PEP 8；新代码尽量保持 flake8 干净（既有 E501 历史问题可不改）。
- 新增功能必须配套测试（文件识别 / 校验逻辑 / 输出解析至少各一例）。
- 提交信息遵循 Conventional Commits（`feat:` / `fix:` / `docs:` / `test:` / `refactor:`）。
- 提交前运行 `python -m pytest`，确保全绿。

## 发布流程（tag 驱动）

1. 更新 `CHANGELOG.md`，补充版本条目。
2. 提交并推送 main。
3. 打 tag 触发 release 工作流：

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

4. `.github/workflows/release.yml` 自动完成：PyPI 发布（Trusted Publishing / OIDC）、
   sigstore 签名、GitHub Release、GHCR 一键体验镜像（v0.26.0 起）。

## 其他

- 架构与设计见 [docs/architecture.md](docs/architecture.md)，Roadmap 见 [docs/roadmap.md](docs/roadmap.md)。
- 插件生态见 [docs/plugins.md](docs/plugins.md)。
- 配置项说明见 [docs/configuration.md](docs/configuration.md)。
