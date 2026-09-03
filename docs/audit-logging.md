# 状态与审计（日志 / 远程推送）

> 迁移自 README 精简版主页；审计相关配置字段见 [配置指南](configuration.md)。[返回 README](https://github.com/Xuqing0415/phase-barrier#readme)

- 状态文件：`<workspace>/.agent_gate/state.json` —— 当前阶段、阶段历史、证据哈希、最近测试结果。
- 审计日志：`<workspace>/.agent_gate/audit.log` —— 结构化 JSON，记录阶段变更、拦截事件、校验结果。
- 证据签名清单：`<workspace>/.agent_gate/evidence_manifest.json` —— 每次阶段推进时记录的证据文件 SHA-256（可选 HMAC 签名），供交付 / CI 用 `verify-evidence` 事后比对（v0.9.0）。
- 审计远程推送：配置 `audit_remote_url` 后，每条审计事件异步 POST 到 SIEM / webhook（单事件为对象，多事件为 JSON 数组），队列有界、失败只计数（v0.9.0）。
- 证据清单导出：`export-evidence` 生成包含清单、当前文件哈希与校验结果的 JSON bundle，可发给第三方审计（v0.10.0）。

示例状态：

```json
{
  "version": 1,
  "current_stage": 2,
  "completed_stages": [0, 1],
  "stage_history": [
    { "stage": 0, "name": "需求接收", "timestamp": "...", "evidence": {"user_request": "..."} },
    { "stage": 1, "name": "Spec 设计", "timestamp": "...", "evidence": {"spec": {"sha256": "..."}} }
  ],
  "evidence": { "user_request": "...", "spec": {}, "tests": {}, "implementation": {}, "last_test_run": {} }
}
```
