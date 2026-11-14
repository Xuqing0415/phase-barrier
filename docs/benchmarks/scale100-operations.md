# Scale-100 运行与收尾操作记录

本文件落实2026-11-14补充方案。样本、预注册协议及正在运行的代码保持启动时的冻结版本；本文件和独立观察器不改变Agent、评分、停止规则或统计。

## 当前恢复状态

2026-11-14 17:34:56 北京时间发生监督器 `PermissionError` 停机，保留23项官方评分；归档、修复、故障注入验收与四项远程CI通过后，于19:29:29启动`resume1`。原Agent及协议继续冻结，新控制器从隔离工作区执行。19:30:07观察器首次复核无告警，下一次定时留档20:30:07。完整时间线及安全恢复入口见[恢复记录](scale100-recovery-2026-11-14.md)。

当前进程身份以`current_launch.json`为准，旧`launch.json`与停机证据保留。恢复启动五分钟证据位于`attempts/resume1/startup_verification.json`，冻结字段为`source_hashes_match`；原首次启动检查格式另行保留。

## 首次运行的历史复核

2026-11-14 17:05北京时间观测：官方评分10/200、完整配对5/100、`test_preservation_intercepts=0`。runner 26288和监督器2156存活、PID与启动记录一致；`halt.json`不存在，运行状态为running。此观测没有评估中途解决率、方向或显著性。

独立观察器为`scripts/scale100_observer.py`，每3600秒观察一次，写入正式输出目录的`hourly_review/`，不停止或重启实验，也不改写原始status、monitor、CSV、补丁或trace。两次计划检查之间只等待时钟，不轮询实验进度。首次人工复核已留档，下一次计划检查为2026-11-14 18:05北京时间。

观察器文件：

- `observer_launch.json`：观察器自身PID、脚本SHA256和周期。
- `latest.json`及`history.jsonl`：四项主字段、进程存活、原始异常列表、冻结哈希和下次计划时间。
- `attention_required.json`：停机、僵尸、连续两小时无新增完成项、首次测试保留拒绝、观察器读取错误或收尾待审阅状态的留档。该文件是本地审阅信号，不是聊天推送或自动恢复。

当前客户端没有可用的定时唤醒对话工具。每小时自动留档不等于每小时由对话Agent完成人工审阅；中断诊断、恢复与最终报告仍需接续审阅。现有运行监督器继续每2秒检查模型/评分故障，首次故障立即停机，独立观察器不接管其职责。

## 小时检查的实际接口

PowerShell，在仓库根目录执行：

```powershell
Get-Content .pytest_tmp/scale100_20261114/hourly_review/latest.json -Encoding UTF8
```

主要读取`completed_jobs`、`completed_pairs`、`test_preservation_intercepts`、`halt_reason`。完整配对仅统计同一实例baseline/gated都已官方graded，不用不同实例数代替配对数。

首次运行的`monitor_status.json`没有`halt_reason`字段，恢复控制器会显式写入；不能仅凭缺失字段断言无停机。观察器结合`status.json`和不早于本次恢复的`halt.json`，同时检查PID及monitor状态，避免把旧停机当成新故障。runner死亡但monitor仍running时，在**观察器自己的记录**标记stale，不覆盖实验原始记录。需要干预的阈值仅生成审阅信号；普通异常、未知空补丁及超过60分钟的记录不会由观察器停止或重跑。

测试保留拒绝从持久化trace的`advance_stage`拒绝错误提取，保留实例ID、turn、文件行号、完整错误及其中可恢复的缺失测试名。若计数为正却没有可恢复的拒绝证据，记录证据缺口，不凭猜测补写测试名。新指标的最终次数仍按预注册的gated stats计数，与观察器发现时间分别报告。

## 只在中断或停滞时处理

先读`status.json`、`current_launch.json`、本次attempt的`supervisor_error.json`、容器及评分日志，并将旧`halt.json`时间与`resumed_at`比较，再决定诊断步骤。本次停机现场已完整归档，原始停机证据继续保留。

归档必须用正式运行根目录的**同级目录**。禁止把`$ROOT`递归复制到`$ROOT/recovery_*`，防止源目录包含目标目录。保留原始CSV、label、stats、patch、prediction、report、trace和启动快照；不得覆盖旧label。

当前恢复分类器从隔离控制器工作区调用：

```powershell
<原执行工作区的Python> -X utf8 scripts/scale100_classify_existing.py `
  --root <正式运行根> --manifest <原冻结清单> --output <新的审阅计划路径>
```

分类器逐项比较官方与Agent证据，不能用旧Verified20默认清单或label替代。恢复计划不是200项完成证明；已完成的未知空补丁不得自动重新采样。缺失官方证据也不能自动分配unresolved=0。

`scripts/scale100_runtime.py resume`是已实现并通过独立故障注入验收的入口，强制检查归档、分类、发布CI及原输入；`run`继续只支持新输出根。恢复从隔离工作区调用并显式指定`--execution-root`，完整命令见恢复记录。已完整评分项跳过，有可信补丁项只补评分，中断Agent使用新attempt标签；本次`resume1`已存在，不得重复启动。若再次出现`supervisor_error`，停止并回到诊断，不自动反复恢复。

## 完成后的核验入口

`scripts/scale100_analysis.py`逐项核验200个唯一(instance_id, mode)键、100配对、patch/stats/prediction/report、gated trace/state及冻结源码，并写`completion_verification.json`。恢复完成后从隔离控制器工作区运行以下入口；监督器也会自动执行相同收尾步骤：

```powershell
<原执行工作区的Python> -X utf8 scripts/scale100_analysis.py `
  --manifest <原冻结清单> --run-root <正式运行根> `
  --source-root <原冻结执行工作区> --cost-exclusions <已审阅的成本排除JSON>
```

只在200项完成后执行。失败时先审阅缺失证据与原始运行，不计算完整配对统计，不将缺失证据记为失败解决。预注册核对同时检查提交历史和工作区；镜像核对同时比较已冻结来源证明与实际使用的ID/RootFS。原首次启动检查字段为`checks.source_hashes_unchanged`，恢复检查为`source_hashes_match`，均只表示启动后5分钟；收尾须重新比对原执行工作区与`launch.json.source_hashes`，不能把启动检查当作收尾检查。

监督器正常完成后会查询Windows电源事件，生成`host_power_events.json`、`cost_exclusions.json`，再运行上述完整性核验与统计，最终状态为待报告审阅。异常或失败也必须保留记录，不能把进程退出等同于完成。

## 固定的统计与报告口径

以44fb2a8提交的`scale100-statistical-protocol.md`为准，不用补充方案示例替换预注册计算：

- McNemar使用双侧精确检验，b=gated-only、c=baseline-only。OR=b/c，95%区间先求Clopper-Pearson二项概率区间，再将两端分别映射为p/(1-p)。Beta分位数本身不是OR区间；b=c=0时OR不可估计；c=0且b>0时仍可给出有限下界和无穷上界。
- 成本点估计是原样本gated P90 / baseline P90，不是bootstrap比值的中位数。10000次bootstrap在各仓库内对实例配对抽样，抽中一对时同时保留两组耗时，seed=2026；不能独立重采样两组数组。分层保持预注册仓库配额，不把它解释为仓库等权。
- 仅有外部中断证据时从主成本排除整对，同时保留全部原始墙钟敏感性；不按耗时猜测或扣减睡眠。规划100对不保证显著性或对某种效应大小的检验效力。
- 历史三批只并列。历史数字须从最终报告/完整官方证据重新核对，尤其区分20对全量、18对独立子集及成本有效样本，不直接抄示例表；不pooled。
- 每个gate完成但官方未解决实例、gate未完成但官方解决实例、零拦截、测试保留拒绝及长耗时实例都须审阅。超过15个可按类别合并呈现，但保留逐项证据与分类，不能用类别合并替代逐项核查。
- 双侧差异检验不显著不能证明“不降低解决率”，也不构成非劣效性检验。结论按实际方向与p值书写；支持、无显著差异或方向反转均须如实呈现。

完整报告必须包含主表、成本及敏感性、新机制、历史并列、异常分类、中断恢复和局限。只有证据、成本及异常审阅完成后，才发布最终报告与统计产物并等待CI；当前已落实安全续跑和运行观察，完整结果、报告及v1.2.1仍待验收。
