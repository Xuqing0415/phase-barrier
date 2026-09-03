# GitHub Action 门禁（CI 集成）

> 迁移自 README 精简版主页；上架与发布流程见 [publish-to-marketplace](publish-to-marketplace.md)。[返回 README](https://github.com/Xuqing0415/phase-barrier#readme)

仓库根目录提供复合 Action（`action.yml`），可直接把 phase-barrier 作为 CI 阶段闸门：
Agent 产出的工作区未达到期望阶段时，CI 直接失败。

```yaml
# 示例：PR 时要求工作区至少完成“实现代码”（阶段 3）
- uses: Xuqing0415/phase-barrier@v0.26.0
  with:
    workspace: .          # 工作区路径（相对仓库根）
    expected_stage: 3     # 0-6；当前阶段 < 期望阶段则失败
    # config: gate.yaml   # 可选：phase-barrier YAML 配置（含 coverage_threshold 等）
```

| 输入 | 默认 | 说明 |
|------|------|------|
| `workspace` | `.` | 工作区路径（相对仓库根） |
| `config` | 空 | YAML 配置文件路径 |
| `mode` | `inspect` | `inspect` 检查阶段；`advance` 推进到 `--to`；`exec` 经门禁执行 `command`，`check` 只读校验是否放行（v0.22.0）；`verify` 校验本次 PR 变更未篡改证据文件（v0.26.0） |
| `expected_stage` | `6` | inspect 模式：当前阶段低于该值则失败 |
| `to` | 空 | advance 模式的目标阶段（必须等于当前阶段 + 1） |
| `command` | 空 | exec 模式的测试/校验命令（仅 mode=exec 必填，v0.18.0） |
| `stage` | 空 | check 模式的阶段号 0-6，校验是否放行进入该阶段（v0.22.0） |
| `cwd` | 空 | exec 模式的工作目录（相对 workspace，可选；v0.19.0） |
| `git_base` | PR 基线 SHA | verify 模式的 Git 基线 ref，默认 `${{ github.event.pull_request.base.sha }}`（v0.26.0） |
| `user_request` | 空 | advance 首次初始化时记录的用户需求原文 |
| `version` | 空 | 安装的 phase-barrier 版本（留空取最新版） |
| `local` | `false` | 安装本地仓库代码而非 PyPI（CI 自测用） |

**参数联动（v0.25.0）**：`mode` 决定需要哪些参数——`advance` 需配 `to`，`check` 需配 `stage`，`exec` 需配 `command`；不满足时门禁直接失败并输出 `::error::`。

**Action 输出（v0.25.0）**：门禁步骤通过后会输出 `workspace` / `stage` / `allowed`，下游步骤可通过 `steps.gate.outputs.*` 复用：

```yaml
- uses: Xuqing0415/phase-barrier@v0.26.0
  id: gate
  with:
    workspace: .
    expected_stage: 3

- name: 复用门禁输出
  run: |
    echo "当前阶段: ${{ steps.gate.outputs.stage }}"
    echo "是否放行: ${{ steps.gate.outputs.allowed }}"
```

`stage` 取值：`inspect` = 当前阶段，`check` = 输入 `stage`，`advance` = 目标 `to`，`exec` 模式为空。



**输入校验（v0.14.0 / v0.18.0 / v0.23.0 / v0.26.0）**：`mode` 必须是 `inspect` / `advance` / `exec` / `check` / `verify`；`expected_stage` 与 `advance` 模式的 `to` 与 `check` 模式的 `stage` 必须是 0-6 的整数；`workspace` 必须存在。参数非法时 CI 直接失败并输出 `::error::` 定位信息，避免静默误判。

完整示例见 `examples/github-action/gate.yml`（通用）、`gate-go.yml`（Go）、
`gate-rust.yml`（Rust）、`gate-pr.yml`（PR 增量校验，v0.26.0）；Go / Rust 示例额外安装 `setup-go` / `rust-toolchain`，
让 `advance` 模式能用真实 `gofmt` / `cargo check` 校验实现。本项目 CI 自带
`gate-action` 自测 job，验证“达到期望阶段通过 / 未达到失败”两条路径。

该 Action 已发布到 [GitHub Marketplace](https://github.com/marketplace/actions/phase-barrier-gate)
（已确认上架：Marketplace 页面显示 **Phase-Barrier Gate**，Latest 版本与 GitHub Release 同步）：
每次打 `v*` tag 时 release 工作流自动创建 GitHub Release（附 CHANGELOG 摘要与发行包），
Action 随之自动上架，用户可直接在 Marketplace 搜索 **Phase-Barrier Gate** 使用。 上架与发布流程详见 `docs/publish-to-marketplace.md`。
