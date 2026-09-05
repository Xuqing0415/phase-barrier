# 插件状态页

插件索引（`plugins.json`）当前收录条目的状态展示，由周期
`plugin-verification.yml`（每周一 03:00 UTC，也可手动 `workflow_dispatch`
触发）自动维护：

1. `auto_discover_plugins.py --update`：按 GitHub topic `phase-barrier-plugin`
   自动发现并验证新插件；对已收录自动条目的新提交做**增量刷新**（v0.47.0，
   `git ls-remote` 对比 `last_commit_sha`，仅上游有变化时重新 clone / 安装 /
   验证并更新入口点）。
2. `verify_plugins.py --update --sync-docs`：全量安装并验证全部条目，刷新下表。

运行 `python scripts/verify_plugins.py --sync-docs`（或周期 workflow）后，下表由
`plugins.json` 自动生成并随提交更新；手动修改会被下一次同步覆盖。

<!-- plugins-index:start -->
| 插件 | 来源 | 收录 | 入口点 | 状态 | 最近验证 | 提交 |
|------|------|------|--------|------|----------|------|
| phase-barrier-foo-adapter | `./examples/custom_adapter` | 官方/人工 | languages: foo | passed | 2026-09-05T09:20:23Z | — |
| phase-barrier-plugin-example | `./examples/plugin_rules` | 官方/人工 | validators: strict_design; interceptors: deny_vendor | passed | 2026-09-05T09:20:23Z | — |
| Xuqing0415/phase-barrier-plugin-template | `https://github.com/Xuqing0415/phase-barrier-plugin-template` | 自动发现 | integrations: demo_integration; interceptors: deny_vendor; languages: demo; validators: require_design_review | passed | 2026-09-05T09:20:23Z | 6208a4e6 |
<!-- plugins-index:end -->

## 状态说明

- 收录列：`自动发现` = 由 GitHub topic 自动发现收录（附收录时验证的提交 SHA）；
  `官方/人工` = 仓库内置官方示例或经 Issue 人工审核收录。
- 状态列：`passed` / `failed` / `unverified`（未声明入口点的占位条目不自动判定）。
- 提交列：自动收录条目最近一次验证的提交 SHA 前 8 位；官方/人工条目为 `—`。

## 失效与处置

- 验证失败的条目状态会写回 `plugins.json` 并随周期提交暴露，不会静默。
- 自动发现当前“只增不删”：插件作者移除 topic 或仓库归档后，条目仍保留并显示
  最近验证结果；自动清理属于后续规划。