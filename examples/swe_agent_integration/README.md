# SWE-agent 集成示例（v0.27.0）

SWE-agent 的工具机制允许把“写文件 / 执行命令”下沉到外部脚本。本示例提供
`gate_tool.py`：一个零依赖 CLI 包装，把请求转发给 phase-barrier sidecar 的
`/api/write` / `/api/exec` / `/api/advance`。

## 运行

```bash
python examples/swe_agent_integration/gate_tool.py --self-test
```

## 手工使用

```bash
export PB_SIDECAR_URL=http://localhost:8080     # sidecar 地址
python gate_tool.py write spec.md < spec.md      # 经门禁写文件（stdin 传内容）
python gate_tool.py exec python -m pytest -q     # 经门禁执行命令
python gate_tool.py advance 2                    # 申请进入阶段 2
```

被拦截时退出码 3 并输出 `GATE_DENIED: <原因>`，SWE-agent 可据此调整计划。

## 接入 SWE-agent

- 把 `gate_tool.py` 复制进 SWE-agent 工作区，按 `swe_agent_example.yaml`
  注册 `gate_write` / `gate_exec` 两个工具（字段按你的 SWE-agent 版本微调）。
- sidecar 部署方式见 `deploy/helm/phase-barrier/README.md`（Helm chart，同 Pod 内
  `localhost:8080` 访问）。
- 更细的“阶段推进”策略建议在编排器钩子层做（见 `examples/orchestrator_hooks/`）。