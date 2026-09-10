# 定向邀请（C2）素材包

目标：让 `plugins.json` 里出现**真实的第三方插件**（不只官方模板 / 种子插件）。
本文件提供可直接复制发送的文案与跟踪表；发送动作需要维护者本人的账号，无法自动完成。

前置事实（可直接引用，均已实证）：

- 打上 GitHub topic `phase-barrier-plugin` 即被每周一 03:00 UTC 的
  `plugin-verification.yml` 自动收录，无需人工合并 PR。
- 该链路已闭环验证：`Xuqing0415/phase-barrier-plugin-foo-adapter` 于 v0.56.0 被自动发现收录，
  随后推送新提交，索引里的 `last_commit_sha` 由 `ec2a9921` 自动刷新为 `175b0002`。
- 收录只校验入口点可用性（`git clone` -> `pip install -e` -> `plugin-verify`），不审查代码质量。
- 快速上手：[docs/plugins.md](../plugins.md) 的「5 分钟创建你的第一个插件」。

## 一、目标对象

| 类型 | 说明 | 切入方式 |
|------|------|----------|
| 编码 Agent 框架作者 | 自己实现 Agent 循环、缺流程约束的项目（如 Alpha-SWE 等 SWE-agent 生态） | 提 Issue 或 Discussion，附最小集成示例 |
| 语言工具链维护者 | 想给某个语言加阶段门禁但不想改主仓库 | 邀请发布语言适配器插件 |
| 校验器 / 规则作者 | 已有 lint / policy 规则，可作为 `phase_barrier.validators` 或 `interceptors` 入口点 | 邀请打包成插件 |
| 内部平台团队 | 想要平台侧集成（hooks / sidecar 客户端） | 邀请发布 `integrations` 插件 |

## 二、可直接发送的文案

### A. GitHub Issue / Discussion 留言（在「如何让 Agent 不跳过测试」类讨论下）

```text
如果你在找「让 Agent 必须按 需求 → Spec → 测试 → 实现 → 测试 → 交付 推进」的方案，
可以看看 phase-barrier：它在工具调用层做阶段门禁，未完成前置阶段直接拦截，
并给出缺失的证据项。除了阶段门禁，v0.52 起还有五道防线专门拦「隐性约束篡改」
（需求说密码 >= 8 位，它写 spec 时悄悄改成 >= 6 位这种）。

仓库：https://github.com/Xuqing0415/phase-barrier

如果你自己的项目里有想复用的语言适配 / 校验规则 / 拦截规则，可以按
https://github.com/Xuqing0415/phase-barrier/blob/main/docs/plugins.md ，
5 分钟打包成插件，打上 topic `phase-barrier-plugin` 就会被自动收录进索引
（每周自动验证，不用提 PR 合并）。
```

### B. 定向邀请（邮件 / DM，给 Agent 框架作者）

```text
主题：把你的 <项目名> 接入 phase-barrier 插件索引？（5 分钟，自动收录）

你好，

我在维护 phase-barrier（https://github.com/Xuqing0415/phase-barrier ），
一个给编码 Agent 加「阶段门禁」的开源框架：需求 → Spec → 测试 → 实现 → 测试 → 交付，
跳步即在工具调用层被拦截。

看到 <项目名> 在做 <一句话定位>，想问一下有没有兴趣做一个插件？

可以做的方向（任选，工作量约 5 分钟）：
- `phase_barrier.languages`：你们的语言 / 文件类型适配器；
- `phase_barrier.validators`：你们已有的设计或质量校验规则；
- `phase_barrier.interceptors`：禁止某类写入 / 命令的规则；
- `anti_shortcut.integrations`：把门禁接进你们自己的 Agent 循环（编排器钩子）。

流程：照 https://github.com/Xuqing0415/phase-barrier/blob/main/docs/plugins.md 的
「5 分钟创建你的第一个插件」打包 -> 给仓库打 topic `phase-barrier-plugin` ->
下一个周期会被自动发现、验证并写进 plugins.json（无需提 PR 到主仓库）。
索引与状态页：https://github.com/Xuqing0415/phase-barrier/blob/main/docs/plugin-status.md

如果暂时没时间，回复我「先不用」即可，不会再来打扰。
```

### C. 社区帖回复（V2EX / 知乎 / Reddit）

```text
补充一个可以自己扩展的点：phase-barrier 的插件机制不需要改主仓库——
语言适配 / 校验器 / 拦截规则 / 集成插件四类入口点都是 Python entry points，
你自己的仓库打包后打上 topic `phase-barrier-plugin`，每周的 workflow 会自动
clone -> pip install -e -> plugin-verify，通过就写进索引。

入口点没报错就算通过（只看可用性，不审查代码质量）。
文档：https://github.com/Xuqing0415/phase-barrier/blob/main/docs/plugins.md
```

## 三、跟踪表

| 日期 | 对象 | 渠道 | 结果 | 后续 |
|------|------|------|------|------|
|      |      |      |      |      |

## 四、验收（C 组）

- [x] C1：`docs/plugins.md` 提供「5 分钟创建你的第一个插件」，README 链接模板仓库。
- [x] C3：索引中已有第二个非模板、非 `examples/` 的独立仓库条目
      （`Xuqing0415/phase-barrier-plugin-foo-adapter`，`auto_discovered: true`）。
- [ ] C2：至少 1 个**外部**（非官方维护）插件进入索引 —— 需要按上文文案实际发送邀请，
      并把结果登记到跟踪表。
