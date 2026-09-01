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
   git commit -m "feat: v0.25.1 ..."
   git push origin main
   ```

3. 打 tag 并推送，触发 release 工作流：

   ```bash
   git tag v0.25.1
   git push origin v0.25.1
   ```

4. 等待 `.github/workflows/release.yml` 完成（PyPI 发布 + sigstore 签名 + GitHub Release）。
5. 在 Marketplace 页面确认 Latest 版本与 GitHub Release 同步。

## 检查清单

- [ ] `action.yml` 的 `name` / `description` 简短明确，Marketplace 展示可读。
- [ ] `branding` 已设置（icon + color），Marketplace 展示需要。
- [ ] `outputs` 已声明（本 Action：`workspace` / `stage` / `allowed`）。
- [ ] 输入参数的 `default` 与实现一致。
- [ ] 示例 / README 引用的版本号与最新 tag 一致（如 `@v0.25.1`）。
- [ ] CI 的 gate-action 自测 job 覆盖主要模式（inspect / check / advance / exec）。

## 备选方案：独立仓库（phase-barrier-action）

若希望 Action 与主仓库解耦（例如面向不同受众、独立版本节奏），可另建
`phase-barrier-action` 仓库，将 `action.yml` 与集成代码复制过去并维护独立 tag。
当前采用 in-tree 上架，简单且与版本同步；如后续 Action 逻辑复杂化再拆分。