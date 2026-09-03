# 教程：CI 增量门禁（PR 场景）

完整门禁（`expected_stage: 6`）适合合并前总检；PR 高频迭代时，更实用的是**增量校验**：
只对本次变更涉及的阶段证据重新校验，缩短反馈时间。

## 一、核心命令

```bash
# 对比 git_base...HEAD 的变更文件，映射到阶段证据并校验
python -m anti_shortcut verify-evidence --workspace . --git-base origin/main --json
```

- 变更 spec -> 要求重新生成测试；
- 变更测试 -> 要求重新运行测试；
- 变更实现 -> 要求测试运行记录存在且通过；
- 无关文件（README、CI 配置）-> 跳过。

## 二、GitHub Action 用法

```yaml
name: Gate

on:
  pull_request:

jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0   # verify 需要读取基线历史

      - uses: Xuqing0415/phase-barrier@v0.29.1
        with:
          workspace: .
          mode: verify
          # git_base 默认是 github.event.pull_request.base.sha，无需显式指定
```

效果：

- 修改源文件但没更新测试记录 -> Action 失败，并输出具体文件；
- 只改文档 -> 跳过校验，不阻塞 PR。

## 三、完整工作流示例（先增量、后总检）

```yaml
name: PR Gate

on:
  pull_request:

jobs:
  incremental:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with: {fetch-depth: 0}
      - uses: Xuqing0415/phase-barrier@v0.29.1
        with:
          workspace: .
          mode: verify

  full:
    runs-on: ubuntu-latest
    if: github.event.pull_request.draft == false
    steps:
      - uses: actions/checkout@v7
        with: {fetch-depth: 0}
      - uses: Xuqing0415/phase-barrier@v0.29.1
        with:
          workspace: .
          expected_stage: 6
```

## 四、与 CLI / 编排器钩子的组合

```bash
# 本地提交前快速自检
python -m anti_shortcut verify-evidence --git-base origin/main --workspace .

# 编排器在 Agent 每次工具调用前调用 SDK 钩子
python - <<'PY'
from anti_shortcut.sdk import PhaseBarrier
b = PhaseBarrier(workspace=".", config="config.yaml")
b.refresh()                      # 重载最新状态（多 Agent 场景）
ok, msg = b.verify_evidence()    # 等价增量校验（默认 git_base）
PY
```

## 五、优缺点与边界

| 优点 | 缺点/边界 |
|------|-----------|
| 反馈快，只校验变更相关 | 可能遗漏全局影响（改公共工具函数只影响单文件） |
| 与 PR 流程自然契合 | 依赖 Git 历史（`fetch-depth: 0`），shallow clone 会失败 |
| 复用同一套证据校验器 | 首次 PR（无基线）需要 fallback 到全量校验 |

建议策略：**增量门禁做快速反馈 + 合并前全量总检兜底**，两者互补。