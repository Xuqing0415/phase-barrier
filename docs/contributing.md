# 贡献指南

欢迎贡献代码、文档、语言适配器与插件。完整指南见仓库 [CONTRIBUTING.md](https://github.com/Xuqing0415/phase-barrier/blob/main/CONTRIBUTING.md)，
这里给出要点。

## 开发环境

```bash
pip install -e ".[dev]"
python -m pytest                 # 全量测试
python -m flake8 --jobs=1 <files> # 风格检查
```

仓库统一 LF（`.gitattributes` 强制）；Windows 下 `git add` 的 CRLF 规范化提示属正常，可忽略。

## 新增语言适配器

1. 实现 `LanguageAdapter`（文件识别 / `check_syntax` / `analyze_tests` / 输出解析），
   参考 `anti_shortcut/languages/` 下现有实现。
2. 注册到 `LANGUAGE_REGISTRY` 与 `_LANGUAGE_MARKERS`（自动检测）。
3. 编写测试并保证覆盖率 ≥ 90%。

## 新增拦截规则 / 校验器

- 拦截规则签名：`rule(kind, target, config, stage, content=None) -> (bool, str) | None`
- 校验器：`{stage: fn}` 映射或带 `stage` 属性的可调用
- 内置规则包见 `anti_shortcut/rules/`，自定义规则可通过入口点或 YAML `rules:` 启用

## 插件开发

插件可通过入口点注册，`python -m anti_shortcut plugin-verify` 自动验证。
模板与提交流程见 [插件与生态](plugins.md)。

## 发布流程

版本号由 git tag 驱动：

```bash
git tag vX.Y.Z && git push origin vX.Y.Z
```

触发 CI + release 工作流（PyPI Trusted Publishing + sigstore 签名 + GitHub Release）。
每次发版前更新 [更新日志](changelog.md)。