# 社区推广内容包（v0.56.0）

任何人可直接复制本目录内容发布到对应平台。发布前请刷新下方「核心数据」中的数字：

```bash
python -m pytest tests --collect-only -q | tail -n 1      # 测试总数（当前 1195，70 个文件）
python scripts/verify_plugins.py --json                    # 插件索引状态（当前 4 条，全 passed）
python scripts/check_custom_domain.py                      # 域名状态（可选）
```

## 一句话简介

> phase-barrier：强制编码 Agent 遵循工程师 SOP 的阶段门禁框架——需求 → Spec →
> 测试 → 实现 → 测试 → 交付，一步都不能跳。

## 长简介（约 160 字）

phase-barrier 是一个阶段门禁反捷径校验框架：通过语言适配、工具拦截与证据校验，
强制编码 Agent 按标准工程师流程工作（先写 Spec、先写测试、再实现、跑通测试才交付）。
v0.52.0 起叠加「五道防线」——需求模板 / 双模型交叉复核 / 形式化约束校验 / 运行时
行为审计 / 概率人工复核，专门拦截 Agent 的**隐性约束篡改**（字面上不违反需求，
却在 spec 或实现里悄悄弱化、删除、替换关键限制）。支持 13 种语言适配器、覆盖率
门禁、安全规则包、HMAC 防篡改、审计日志，并提供 GitHub Action、K8s sidecar
（gRPC/HTTP）、Docker 一键体验与插件 topic 自动收录生态。

## 核心数据（发布前核对）

| 指标 | 数值 | 备注 |
|------|------|------|
| PyPI 最新发布 | v0.51.0 | `pip install phase-barrier`；main 已累积至 v0.56.0（里程碑驱动，见 release.md） |
| main 最新里程碑 | v0.56.0 | 五道防线 + 插件自动收录闭环实证 |
| 支持语言 | 13 | Python / JavaScript（含 TypeScript）/ Java / Kotlin / Scala / Go / Rust / Swift / Ruby / PHP / C#（.NET 别名）/ C++ / Dart |
| 测试 | 1195（70 个文件） | 覆盖率门禁 ≥90%（实测 94%） |
| CI 矩阵 | Linux / Windows / macOS × Python 3.11-3.14 | 真实语言工具链全量激活 |
| SWE-bench 双组实测 | Scale-20：baseline 25% vs gated 25%（19 次拦截）；官方容器内新增 10 例 gated 50% > baseline 40% | 见 `docs/tutorials/swe-bench-real.md` §7.4 / §8 / §9 |
| 插件生态 | `plugins.json` 4 条（2 官方示例 + 2 自动发现），全部 passed | 打 `phase-barrier-plugin` topic 即被每周自动收录；增量刷新已实证 |
| 分发 | PyPI + GitHub Release（sigstore 签名） | GitHub Action Marketplace |
| 文档站 | <https://docs.xshayncka.dev/> | MkDocs |
| 一键体验 | `docker run --rm -it ghcr.io/xuqing0415/phase-barrier-demo` | 零安装 |

## 平台帖子模板

### X / Twitter

```text
编码 Agent 写代码很快，但会跳过"先写测试"吗？

phase-barrier 用阶段门禁强制 Agent 遵循工程师 SOP：
需求 → Spec → 测试 → 实现 → 测试 → 交付，一步不能跳。

v0.52 起还有"五道防线"，专抓 Agent 的隐性约束篡改
（需求说密码 ≥8 位，spec 悄悄写成 ≥6 位这种）。

13 种语言 / GitHub Action / K8s sidecar / 插件自动收录
开源：https://github.com/Xuqing0415/phase-barrier
```

### LinkedIn

```text
【让编码 Agent 按流程交付】phase-barrier 是一个开源阶段门禁框架……

问题：Agent 高频产出却常跳过设计、测试与回归。
方案：在 Agent 工具调用层加阶段门禁——写文件 / 执行命令前先校验当前阶段证据，
未完成前置阶段直接拦截，并提供明确提示。

亮点：
· 13 种语言适配器（Python/JS/TS/Java/Kotlin/Scala/Go/Rust/Swift/Ruby/PHP/C#/C++/Dart）
· 五道防线反「隐性约束篡改」：需求模板 → 双模型交叉复核 → 形式化校验 →
  运行时行为审计 → 概率人工复核
· SWE-bench 双组实测（baseline vs gated）公开可复算
· HMAC 状态签名、覆盖率门禁、审计日志、K8s sidecar (gRPC/HTTP)、GitHub Action
· 插件生态：打 `phase-barrier-plugin` topic 即被每周自动收录

仓库：https://github.com/Xuqing0415/phase-barrier
```

### Reddit（r/Programming / r/LocalLLaMA 风格，注意平台规则）

```text
I built a stage-gate framework that stops coding agents from skipping tests.

Most agent demos show "agent writes code and passes". Real teams worry about
agents that skip specs/tests/regression — and, worse, quietly weaken the
constraints they were given (requirement says ">= 8 chars", the spec it writes
says ">= 6").

phase-barrier wraps the agent's tools with phase gates: no spec -> no tests ->
no implementation -> no delivery. On top of that v0.52 added five defence lines
(requirement template, dual-model cross review, formal constraint checking,
runtime behaviour audit, probabilistic human review).

Supports 13 language toolchains, ships as PyPI package + GitHub Action + K8s
sidecar, and auto-indexes third-party plugins via the `phase-barrier-plugin`
topic. CI runs the real toolchain matrix (Linux/Windows/macOS). We also publish
baseline-vs-gated SWE-bench numbers (20 instances) with the raw CSVs.

Repo: https://github.com/Xuqing0415/phase-barrier
```

### V2EX / 知乎

```text
编码 Agent 越来越强，但怎么保证它不乱来？
分享一个开源方案 phase-barrier：

- 给 Agent 的工具调用加"阶段门禁"：Spec/测试/实现/回归 按 SOP 推进，跳步即拦截
- 五道防线专治"隐性约束篡改"：需求里的限制条件被悄悄弱化/删除时能被抓出来
- 语言无关：Python、JS/TS、Java、Kotlin、Scala、Go、Rust、Swift、Ruby、
  PHP、C#、C++、Dart 共 13 种适配
- 落地方式多：pip 包 + CLI、GitHub Action、K8s sidecar（HTTP/gRPC + mTLS）
- 有实测数据：SWE-bench 双组（baseline/gated）评测 20 实例，原始 CSV 可复算
- 生态开放：插件打 `phase-barrier-plugin` topic，每周自动验证并收录

仓库 / 文档：https://github.com/Xuqing0415/phase-barrier
欢迎体验和提 Issue。
```

### Hacker News（Show HN 标题与正文）

```text
Show HN: phase-barrier – phase gates that stop coding agents from skipping tests

https://github.com/Xuqing0415/phase-barrier

Coding agents are good at producing code and bad at following a process. This
is a Python framework that wraps the agent's file/command tools with phase
gates: you cannot write implementation before tests exist, cannot deliver
before the suite is green.

v0.52 added five "defence lines" aimed at a specific failure mode: an agent
that technically satisfies the requirement text while quietly weakening the
constraints (e.g. requirement says password >= 8 chars, spec says >= 6). Those
lines are: structured requirement template, dual-model cross review (coverage
check + tamper check, fail-closed), formal constraint checking (TLA+/TLC with a
YAML DSL), runtime behaviour audit (tool-call trace vs declared behaviour), and
probabilistic human review.

We measured it: 20 SWE-bench-Lite instances run twice (baseline vs gated) plus
a Verified-slice re-check. Numbers and caveats are in the repo; the samples are
small and we say so.

Everything is offline-first: pip package, GitHub Action, K8s sidecar, and a
Docker image for a zero-install demo.
```

## 视频素材（B2）

**成片已生成**：`docs/media/phase-barrier-demo.mp4`（1280x720 / 150 秒 / 约 0.9 MB）。
画面来自 `docker/demo/agent_demo.py` 的真实运行输出，由
`python scripts/make_demo_video.py` 自动生成，可一条命令重录。

- 内容结构：标题钩子（0:00-0:15）→ 问题（0:09-0:15）→ **真实终端回放**（0:15-0:46，含
  「跳步被拦截」红字与「按 SOP 全通到交付」绿字）→ 五道防线（0:46-1:12）→
  13 语言 / SWE-bench 实测 / 插件生态（1:12-1:36）→ 快速开始（1:36-2:02）→ CTA（2:02-2:30）。
- 成片为**无声**，可直接用平台自动字幕，或在 B 站/剪映配音后发布。
- 录制与分镜清单（如需 OBS 真人讲解版）：
  [docs/video-tutorial-template.md](../video-tutorial-template.md)。
- 上传后把链接填进下方「发布记录表」，并同步到 README。
## 发布记录表

| 日期 | 平台 | 链接 | 反馈 |
|------|------|------|------|
|      |      |      |      |

## 待办（需要维护者账号，无法自动完成）

- [ ] B2：演示视频已生成（`docs/media/phase-barrier-demo.mp4`），剩余步骤是**上传**到
      B 站 / YouTube 并回填链接（需要维护者账号）。
- [ ] B3：把本目录模板发布到 ≥3 个平台（Dev.to / Reddit / HN / V2EX / 知乎）。
- [ ] C2：定向邀请（把 `docs/plugins.md` 的「5 分钟创建你的第一个插件」
      发给编码 Agent 社区活跃者 / Alpha-SWE 相关开发者），邀请记录写在本文件。
