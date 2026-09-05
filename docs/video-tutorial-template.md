# 视频教程模板

> 面向维护者 / 贡献者的宣传视频制作模板（v0.48.0）。目标：任何人在 30 分钟内
> 按本模板产出一条 2-5 分钟可发布的教程视频，无需剪辑经验。

## 1. 视频目标与定位

- 受众：编码 Agent 使用者 / 关心“Agent 如何遵守开发流程”的工程师。
- 核心信息：phase-barrier 把“先设计 -> 先写测试 -> 再实现 -> 回归”的工程师
  SOP 变成不可跳过的阶段门禁。
- 时长：2-5 分钟；一句话钩子 + 一次完整演示 + 安装方式 + 行动号召。

## 2. 脚本大纲（可直接套用）

```text
[0:00-0:15] 钩子
   "如果你的编码 Agent 会跳过测试直接写代码——这是强制它按工程师流程走的门禁。"

[0:15-0:50] 问题演示（关键镜头）
   在终端让 Agent 试图"直接进入实现阶段"（跳过 spec / 测试），
   展示 phase-barrier 拦截输出：门禁未通过 + 提示缺失证据。

[0:50-1:40] 正确流程演示
   按 SOP 推进：spec.md -> tests -> 实现 -> 测试运行 -> 进入交付，
   展示 `python -m anti_shortcut inspect / advance / verify-evidence`。

[1:40-2:20] 快速开始
   方式一（零安装）：docker run --rm -it ghcr.io/xuqing0415/phase-barrier-demo
   方式二（项目内）：pip install phase-barrier && python -m anti_shortcut init

[2:20-2:50] 进阶亮点（任选 1-2 个）
   - 多语言：13 种语言适配器（Python/JS/Go/Rust/Java/...）
   - CI 门禁：GitHub Action marketplace 中的 phase-barrier
   - K8s sidecar / gRPC / 审计日志 / HMAC 防篡改
   - 插件生态：GitHub topic `phase-barrier-plugin` 自动收录

[2:50-3:00] 行动号召
   Star / 文档站（xuqing0415.github.io/phase-barrier）/ 插件投稿 / 提 Issue
```

## 3. 录制环境准备

- 演示环境：使用一键体验镜像，无需本地安装任何语言工具链：

  ```bash
  docker run --rm -it ghcr.io/xuqing0415/phase-barrier-demo
  ```

- 本地演示工作区：`examples/demo_workspace/`（含 spec / 测试 / 实现示例）。
- 建议准备 80x24 以上终端窗口、深色主题、大字号（14+），提前 `cls`/`clear`。
- 敏感信息：不要录入真实 token / 密钥；示例用假值。

## 4. 推荐工具

| 用途 | 工具 | 说明 |
|------|------|------|
| 录屏 | OBS Studio（免费） | 录制区域选择终端窗口即可 |
| 快速录制 | Loom / ScreenRec | 录完即分享链接，适合初稿 |
| 剪辑 | CapCut / iMovie / 剪映 | 裁剪开头结尾、加字幕 |
| 字幕 | 剪映自动字幕 / OBS 插件 | 中文内容建议开字幕 |
| 演示 | Windows Terminal / iTerm2 | 保证字体渲染清晰 |

## 5. 成片检查清单

- [ ] 开头 15 秒内讲清“解决什么问题”
- [ ] 出现至少一次“被拦截”与一次“按流程通过”的对照
- [ ] 命令真实可复现（不要录假输出）
- [ ] 关闭通知，避免录到弹窗
- [ ] 分辨率 ≥1280x720，字幕无错别字
- [ ] 结尾给出文档站 / 仓库链接

## 6. 发布建议

- 平台：B 站 / YouTube / X 短视频；社区：V2EX / 知乎 / Reddit。
- 发布文案可直接使用 `docs/promotion/README.md` 中的帖子模板。
- 发布后把链接补充到 README「媒体与教程」区域或 docs 首页（如有）。
- 如录制了新的官方示例，欢迎提 PR 更新本模板中的演示命令与数据。