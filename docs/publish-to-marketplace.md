# 发布到 GitHub Marketplace

本仓库的 `action.yml` 位于默认分支（main），每次创建 GitHub Release 时，
GitHub 会自动将仓库 Action 上架 / 更新到 Marketplace（in-tree 上架），
用户可直接在 [Marketplace](https://github.com/marketplace/actions/phase-barrier-gate) 搜索
**Phase-Barrier Gate** 使用。

## 前置条件

- 已接受 GitHub Developer 协议（首次发布到 Marketplace 需在 GitHub 设置中签署）。
- 仓库为 public，`action.yml` 位于默认分支根目录。
- `action.yml` 合法：`name`、`description`、`branding`、`inputs`、`outputs`、`runs` 齐全。
- Release 工作流（`.github/workflows/release.yml`）能正常创建 GitHub Release。

## 发布流程（in-tree 上架，推荐）

1. 更新 `CHANGELOG.md`，补充版本条目。
2. 提交改动并推送 main：

   ```bash
   git add -A
   git commit -m "feat: v0.26.3 ..."
   git push origin main
   ```

3. 打 tag 并推送，触发 release 工作流：

   ```bash
   git tag v0.26.3
   git push origin v0.26.3
   ```

4. 等待 `.github/workflows/release.yml` 完成（PyPI 发布 + sigstore 签名 + GitHub Release）。
5. 在 Marketplace 页面确认 Latest 版本与 GitHub Release 同步。

## 检查清单

- [ ] `action.yml` 的 `name` / `description` 简短明确，Marketplace 展示可读。
- [ ] `branding` 已设置（icon + color），Marketplace 展示需要。
- [ ] `outputs` 已声明（本 Action：`workspace` / `stage` / `allowed`）。
- [ ] 输入参数的 `default` 与实现一致。
- [ ] 示例 / README 引用的版本号与最新 tag 一致（如 `@v0.26.3`）。
- [ ] CI 的 gate-action 自测 job 覆盖主要模式（inspect / check / advance / exec）。

## 元数据最佳实践（v0.44.0）

`action.yml` 的元数据决定 Marketplace 展示、搜索可发现性与工作流编辑器的参数提示，维护时遵循：

- **description**：英文且精简（50-200 字符），包含核心关键词
  （如 `stage gate` / `coding agent` / `SOP` / `CI`），不重复 action name。
- **author**：指向 GitHub 用户名 / 组织（本仓库为 `Xuqing0415`）。
- **branding**：`icon` + `color` 必填，且在 GitHub 允许集合内
  （icon 例： `shield` / `lock` / `check`；color 例： `blue` / `green`）。
- **inputs / outputs**：每个参数都要有单语句 `description`
  （用途 + 示例值 + 面向那个 mode）；composite action 的
  `outputs` 必须用 `value: ${{ steps.<id>.outputs.<name> }}` 映射。
- **自动校验**：`tests/test_action_meta.py` 保证以上约束（长度 /
  关键词 / author / branding / 参数描述），改动 `action.yml` 后请跑该测试模块。

## 备选方案：独立仓库（phase-barrier-action）

若希望 Action 与主仓库解耦（例如面向不同受众、独立版本节奏），可另建
`phase-barrier-action` 仓库，将 `action.yml` 与集成代码复制过去并维护独立 tag。
当前采用 in-tree 上架，简单且与版本同步；如后续 Action 逻辑复杂化再拆分。