# 自定义域名部署（可选）

phase-barrier 官方文档站默认托管在 GitHub Pages：

- 默认地址：<https://xuqing0415.github.io/phase-barrier/>
- 可选域名：`docs.phase-barrier.dev`（**未启用**时为可选项，不配置不影响任何功能）

本文说明如何把 `docs.phase-barrier.dev` 指向 GitHub Pages，并提供自动化检查脚本。
配置前请确认你拥有该域名的 DNS 控制权；**在 DNS 生效前不要提交 `CNAME` 文件**，
否则 GitHub Pages 会因无法校验域名而暂时中断默认地址访问。

## 工作原理

MkDocs 会把 `docs/` 目录下除 Markdown 外的文件原样复制到站点根目录。
因此只需在仓库内维护 `docs/CNAME`（内容为一行域名），部署 workflow
（`.github/workflows/docs.yml`）推送到 `gh-pages` 分支后，GitHub Pages
读取站点根目录的 `CNAME` 即自动应用自定义域名。

## 配置步骤（约 10 分钟）

1. **DNS 解析**：到域名服务商添加一条 CNAME 记录

   | 主机记录 | 类型 | 记录值 |
   |----------|------|--------|
   | `docs` | CNAME | `xuqing0415.github.io` |

2. **添加 CNAME 文件**：在仓库根目录执行

   ```bash
   echo docs.phase-barrier.dev > docs/CNAME
   git add docs/CNAME
   git commit -m "chore: enable docs.phase-barrier.dev custom domain"
   git push origin main
   ```

   `docs.yml` 部署完成后，用下面命令确认站点内已带 CNAME：

   ```bash
   python scripts/check_custom_domain.py
   ```

3. **GitHub Pages 设置**：仓库 `Settings -> Pages -> Custom domain` 填入
   `docs.phase-barrier.dev` 并保存。GitHub 会校验 DNS 并为该域名签发 HTTPS
   证书（通常几分钟，最长约 24 小时）。
4. **强制 HTTPS**：证书签发成功后勾选 `Enforce HTTPS`。
5. **验证**：

   ```bash
   curl -I https://docs.phase-barrier.dev/plugin-status/   # 期望 HTTP 200
   python scripts/check_custom_domain.py --strict           # 期望 exit 0
   ```

## 自动化检查

`scripts/check_custom_domain.py`（纯标准库）检查 `docs/CNAME` 是否存在且内容
与期望域名一致：

```bash
python scripts/check_custom_domain.py            # 未配置时打印警告但 exit 0
python scripts/check_custom_domain.py --strict   # 未配置 / 配置错误时 exit 1
python scripts/check_custom_domain.py --cname path/to/CNAME --domain example.com
```

- 默认非阻塞：CI（`docs.yml`）每次构建都会运行一次，未配置时仅输出
  `::warning::`，不影响部署。
- `--strict` 供本地验收 / 发布前检查使用：未配置或内容不一致时返回非零。
- 该检查只验证仓库内 `CNAME` 文件；真实域名解析与证书签发由 GitHub
  Pages 负责，需在网页端确认。

## 回滚

删除 `docs/CNAME` 并推送，下一次 `docs.yml` 部署会自动恢复
`https://xuqing0415.github.io/phase-barrier/` 访问；如需同时移除 GitHub
Pages 设置里的自定义域名，在 `Settings -> Pages` 中清除即可。

## 故障排查

| 现象 | 原因与处理 |
|------|------------|
| `curl https://docs.phase-barrier.dev` 报 DNS 解析失败 | CNAME 未生效：检查 DNS 记录与解析传播（`nslookup docs.phase-barrier.dev`） |
| GitHub Pages 提示 "domain does not resolve" | DNS 记录值写错或指向了 `github.io` 之外的地址 |
| 配置后默认地址 `xuqing0415.github.io/...` 暂时 404 | 自定义域名证书签发中，属正常现象，等待完成 |
| `check_custom_domain.py --strict` 退出 1 | 仓库内尚无 `CNAME` 或内容与期望域名不一致 |

> **状态（截至 v0.48.0）**：本仓库**未启用**自定义域名，`docs/CNAME` 不存在，
> CI 检查输出非阻塞警告属预期；待域名与 DNS 就绪后按本文启用。