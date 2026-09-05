# 社区推广内容包（v0.48.0）

任何人可直接复制本目录内容发布到对应平台。发布前请刷新下方“核心数据”中的数字：

```bash
python -m pytest --collect-only -q | tail -n 1        # 测试总数
python scripts/check_custom_domain.py                  # 域名状态（可选）
```

## 一句话简介

> phase-barrier：强制编码 Agent 遵循工程师 SOP 的阶段门禁框架——需求 → Spec →
> 测试 → 实现 → 测试 → 交付，一步都不能跳。

## 长简介（约 100 字）

phase-barrier 是一个阶段门禁反捷径校验框架：通过语言适配、工具拦截与证据校验，
强制编码 Agent 按标准工程师流程工作（先写 Spec、先写测试、再实现、跑通测试才交付）。
支持 13 种语言适配器、覆盖率门禁、安全规则包、HMAC 防篡改、审计日志，并提供
GitHub Action、K8s sidecar（gRPC/HTTP）、Docker 一键体验与插件自动收录生态。

## 核心数据（发布前核对）

| 指标 | 数值 | 备注 |
|------|------|------|
| 最新版本 | v0.48.0 | PyPI `phase-barrier`，tag 驱动发布 |
| 支持语言 | 13 | Python/JS/TS/Java/Go/Rust/Ruby/C#/C++/.NET/PHP/Kotlin/Scala/Swift/Dart 等 |
| CI 矩阵 | Linux / Windows / macOS × Python 3.11-3.14 | 真实语言工具链全量激活 |
| 测试 | 900+（发布前以 collect-only 为准） | 覆盖率门禁 ≥90% |
| 分发 | PyPI + GitHub Release（sigstore 签名）| GitHub Action Marketplace |
| 文档站 | <https://xuqing0415.github.io/phase-barrier/> | MkDocs |
| 一键体验 | `docker run --rm -it ghcr.io/xuqing0415/phase-barrier-demo` | 零安装 |

## 平台帖子模板

### X / Twitter

```text
编码 Agent 写代码很快，但会跳过"先写测试"吗？

phase-barrier 用阶段门禁强制 Agent 遵循工程师 SOP：
需求 → Spec → 测试 → 实现 → 测试 → 交付，一步不能跳。

13 种语言适配 / GitHub Action / K8s sidecar / 插件自动收录
开源：https://github.com/Xuqing0415/phase-barrier
```

### LinkedIn

```text
【让编码 Agent 按流程交付】phase-barrier 是一个开源阶段门禁框架……

问题：Agent 高频产出却常跳过设计、测试与回归。
方案：在 Agent 工具调用层加阶段门禁——写文件 / 执行命令前先校验当前阶段证据，
未完成前置阶段直接拦截，并提供明确提示。

亮点：13 种语言适配器、覆盖率门禁、HMAC 状态签名、审计日志、K8s sidecar
(gRPC/HTTP)、GitHub Action、插件 topic 自动收录生态。
仓库：https://github.com/Xuqing0415/phase-barrier
```

### Reddit（r/Programming 风格，注意平台规则）

```text
I built a stage-gate framework that stops coding agents from skipping tests.

Most agent demos show "agent writes code and passes". Real teams worry about
agents that skip specs/tests/regression. phase-barrier wraps the agent's tools
with phase gates: no spec -> no tests -> no implementation -> no delivery.

Supports 13 language toolchains, ships as PyPI package + GitHub Action + K8s
sidecar, and auto-indexes third-party plugins via the phase-barrier-plugin
topic. CI runs the real toolchain matrix (Linux/Windows/macOS).

Repo: https://github.com/Xuqing0415/phase-barrier
```

### V2EX / 知乎

```text
编码 Agent 越来越强，但怎么保证它不乱来？
分享一个开源方案 phase-barrier：

- 给 Agent 的工具调用加"阶段门禁"：Spec/测试/实现/回归 按 SOP 推进，跳步即拦截
- 语言无关：Python、JS/TS、Java、Go、Rust、Ruby、C#、C++、PHP、Kotlin、
  Scala、Swift、Dart 都有适配
- 落地方式多：pip 包 + CLI、GitHub Action、K8s sidecar（HTTP/gRPC + mTLS）
- 生态开放：插件打 `phase-barrier-plugin` topic，每周自动验证并收录

仓库 / 文档：https://github.com/Xuqing0415/phase-barrier
欢迎体验和提 Issue。
```

## 截图素材建议

- 拦截演示：运行 `python -m anti_shortcut inspect` 与一次被拦截的推进，截取终端。
- 状态页：<https://xuqing0415.github.io/phase-barrier/plugin-status/>。
- CI 徽章：仓库 README 顶部（CI / PyPI / Coverage）。
- Docker 演示：`docker run --rm -it ghcr.io/xuqing0415/phase-barrier-demo` 录屏首帧。

## 发布记录表（建议）

| 日期 | 平台 | 链接 | 反馈 |
|------|------|------|------|
|      |      |      |      |