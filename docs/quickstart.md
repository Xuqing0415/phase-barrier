# 快速开始

## 安装

```bash
pip install phase-barrier
```

Python 3.10+，无第三方运行时依赖（语言适配器按需调用系统工具，如 `php` / `gcc` / `node`）。

## 初始化

在工作区根目录生成配置模板（自动检测语言）：

```bash
python -m anti_shortcut init [--language auto] [--output config.yaml]
```

生成 `config.yaml` 后即可开始门禁流程。

## 最小接入（Python API）

```python
from anti_shortcut import AntiShortcutSkill

skill = AntiShortcutSkill(
    workspace=".",
    config="config.yaml",
    user_request="实现一个计算斐波那契数列的函数",
)

# 包装 Agent 的工具（write_file / execute_command），未满足前置阶段时抛 PermissionError
tools = skill.install(agent_tools)

tools["write_file"]("spec.md", spec_content)
tools["advance_stage"](2)          # 校验 spec 证据，进入阶段 2
tools["write_file"]("test_fib.py", tests_content)
tools["advance_stage"](3)          # 校验测试证据，进入阶段 3
tools["write_file"]("fib.py", impl_content)
tools["advance_stage"](4)          # 校验实现语法，进入阶段 4
tools["execute_command"]("pytest -q")
skill.state.mark_test_run({...})   # 记录测试结果
tools["advance_stage"](6)          # 测试通过，交付
```

## CLI 门禁

```bash
# 查看当前阶段
python -m anti_shortcut inspect --workspace . --json

# 推进阶段（校验当前阶段证据）
python -m anti_shortcut advance --to 2 --workspace .

# 门禁执行命令（自动记录测试结果）
python -m anti_shortcut exec --command "pytest -q" --workspace .

# 校验证据未被事后篡改（可配合 CI / Git 基线）
python -m anti_shortcut verify-evidence --git-base origin/main --workspace .
```

完整命令见 [CLI 使用](usage.md)。

## 一键体验（Docker）

无需本地安装 Python / Node 等，直接查看门禁拦截与正常推进的完整过程：

```bash
docker run --rm -it ghcr.io/xuqing0415/phase-barrier-demo
```

镜像内置模拟 Agent 脚本，演示跳步被拦截 + 按 SOP 推进到交付两个场景。

## GitHub Action（CI 门禁）

```yaml
- uses: Xuqing0415/phase-barrier@v0.29.0
  with:
    workspace: .
    expected_stage: 6
```

Agent 产出未按 SOP 完成（阶段 < 6）时 CI 失败；`mode: advance` / `exec` / `verify` 详见
[Marketplace](https://github.com/marketplace/actions/phase-barrier-gate) 与
[集成指南](integrations.md)。

## K8s sidecar

Helm 一键部署，见 [K8s 部署](k8s.md)。