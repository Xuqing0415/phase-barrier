# Scale-100 恢复适配设计（待批准，未实施）

本设计对应第二次评分故障。当前只写设计，不改代码、不运行测试/模拟、不执行恢复。当前98项评分、49对是停机检查点；CI、文档或设计通过都不等于批准恢复。

## 1. 原始停止原因与处置类型映射

| 原始halt.reason | 事件类型提案 | 中断记录与约束 |
| --- | --- | --- |
| `supervisor_error:PermissionError` | `supervisor_permission_error` | 保留第一次停止原因及原归档引用；只有实际受中断Agent才标记对应的interrupted_agent原因 |
| `grade_infra_failure` | `grading_infrastructure_failure` | 保存原reason；分别记录评分失败任务和被连带停止的并行Agent，后者不能记为PermissionError |
| `model_call_error` | `model_infrastructure_failure` | 保存原reason及调用错误证据；不把模型调用异常计为官方unresolved |
| 其他/未知值 | `review_required` | 保留原值并阻断执行，不默认映射到rerun |

事件类型是分类设计，不是底层因果结论。`grading_infrastructure_failure`沿用现有停止类别，不能据此认定已排除Agent补丁问题。若将来明确为补丁问题，保留原reason，另记根因审阅结论；不改写历史halt或CSV。

## 2. 分类器grade_infra_failure分支

### 输入

输入须包含真实halt的路径、SHA256、observed_at、cleanup状态，固定manifest、当前索引、CSV、逐任务Agent/评分标签及可信patch/stats、prediction/report、strict证据、启动与任务/容器关联。每项绑定批准范围和审阅状态；只有补丁存在不足以批准仅评分，没有补丁也不足以批准重跑。

### 输出与批准约束

| 证据情况 | 候选输出 | 是否可执行 |
| --- | --- | --- |
| 已有一致可信的官方graded证据 | `skip` | 保留原结果，任何矛盾先阻断 |
| 有可信完成的Agent补丁，评分缺失/中断 | `regrade_only` | 根因/strict/评分契约审阅通过且明确批准后才可执行；Agent调用必须为0 |
| Agent被外部停止打断，无完成stats/补丁 | `rerun` | 任务、容器、停止事件及批准范围一致才允许新attempt |
| 完成但未知空补丁、矛盾证据、未确认中断或未知原因 | `review_required` | 阻断，不自动重新采样或分配unresolved |
| 补丁自身可能有问题 | `review_required`，可附`excluded`候选 | **不能自动排除**；原因本身不改变预注册样本 |
| 从未开始且证据一致 | `pending` | 仅在整个新恢复计划获准后调度 |

`excluded`不是默认调度动作，也不是缩减100实例的许可。若人工提出排除，必须区分“从主P90排除整对”和“放弃官方评分/改变样本”；后者与200项完整实验要求冲突，必须明确解决该范围再谈恢复，不能将被排除项当作已完成。

候选计划包含任务、原始reason、事件类型、候选动作、审阅状态、证据哈希及未满足条件。未批准时整体`executable=false`。正式可执行计划须在隔离适配、测试/模拟及公开CI通过后另行生成，并重新核对当前证据，不把本文当计划输入。

## 3. 恢复记录和成本映射

拟记录字段包括：原始`halt_reason`、事件类型、受影响阶段（Agent或评分）、源attempt、instance/mode、halt归档相对路径与SHA256、任务日志/标签和停止时点、审阅/批准引用及成本处置状态。

原因从已验证的真实halt读取，不硬编码`supervisor_permission_error_interrupted_agent`。第二次事件记录应与第一次Django-12155事件并存，按实例/模式/attempt/事件去重，不覆盖或串用两次halt。并行Agent中断须独立关联容器，不仅按发生时间猜测。

评分失败不会自动排除已完成Agent耗时；新Agent attempt不包含失败尝试总成本。成本是否排除整对须另行审阅，正式`cost_exclusions.json`只在全部中断审阅后生成。`resume1`日历跨度14,414.25秒仅作事件时间线，不与Agent耗时相加。

新评分标签保留原Agent标签，新Agent用新attempt；不得复用resume1。旧故障行、patch、prediction、report、trace/state及索引历史在新现场归档保留；批准后替换当前故障行也须保留原行来源，不覆盖有效已评分行，不把两次attempt算作两个样本。

## 4. 拟议测试/模拟范围（本轮未执行）

| 测试 | 期望 |
| --- | --- |
| 三类reason与未知reason | 真实值保留，已知值准确映射，未知值阻断 |
| 第二次真实halt fixture | 由保存的halt生成脱敏fixture并记录源哈希；grade故障不误记为PermissionError；不改源文件 |
| 仅评分候选 | 有可信补丁、完整strict/契约审阅及批准才放行；模型调用0，原patch/stats不变 |
| 无补丁/未完成baseline | 只在外部中断证据完整时生成可执行rerun；缺任务/容器关联、cleanup失败或缺批准均阻断 |
| baseline也评分失败的合成对照 | 两个失败标记本身不输出“环境已证实”；须比较相同起点、各自补丁、失败阶段及具体日志；baseline未评分则明确无对照 |
| 证据一致性 | CSV/索引/stats/patch/prediction/report/strict矛盾阻断；缺report不转unresolved，未知空补丁不重采样 |
| 成本与两次停机 | 区分Agent中断和评分中断；两次账本并存且不重复；不自动排除或合计日历跨度 |
| 有效结果/旧故障保护 | 98项官方评分、原23项及既有产物保留；故障行若批准替换保留历史；新标签唯一 |
| 审批及计划变更 | 输入哈希、归档、审批、CI或模拟任一缺失/变化则拒绝；拒绝时正式运行根无写入 |
| 独立进程模拟 | 在`SIMULATION_ONLY`临时合成目录注入评分故障，监督器停止并行Agent；经模拟专用批准后验证仅评分、跳过完成项、混合索引及真实PID停止；真实模型调用0 |

只在隔离代码工作区实施，原Agent、规则、样本、统计协议、模型参数和镜像/起点证明继续冻结。本设计不允许修Pylint补丁、改变正式模型参数、放宽停止规则或自动循环恢复。

## 5. 设计验收与恢复门槛

设计范围已覆盖三类reason、输入/输出、真实halt引用、第二次故障及成本映射。实现、测试、模拟、公开CI与正式计划均**未完成**；设计提交也尚不代表人工审阅通过。

后续仅在明确授权后按以下顺序进行：新现场归档并核对 → 定案处置与证据范围 → 隔离实现 → 测试/模拟 → 公开CI → 新恢复计划（如resume2） → 当前证据一致性审核及批准 → 正式恢复。剩余102项评分已包含Pylint gated故障项，不能再写成“102项另加Pylint处置”。完成200项/100对后再做全量核验、异常成本审阅、预注册统计、报告和v1.2.1。
