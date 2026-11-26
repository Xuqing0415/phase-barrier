# Scale-100 处置范围：待批准审阅记录（2026-11-26）

本记录提交原 `scale100-disposition-draft-20261126.md` 的审阅内容，状态为**待批准，未批准恢复**。它不是正式恢复计划、最终证据验收或统计报告。实验保持停机；没有调用模型、评分器、分类器主入口或 `scale100_analysis.py`，没有修改运行根、冻结源或恢复控制脚本。

当前停机检查点为98/200项官方评分、49/100个完整配对，另有1条评分故障；不作最终统计解释。整体结论为：准备性调查和设计范围已列明，Pylint执行处置需补证，暂不能批准恢复。

附件方案的三项独立产出已提交：[Pylint只读根因初判](pylint-4661-root-cause.md)、[恢复适配设计](scale100-recovery-adapter-design.md)、[成本/异常候选及批准门槛](scale100-cost-anomaly-candidates.md)。补丁应用失败已定位到官方测试执行前；负向夹具删除已确认，baseline无评分对照、前三次git apply完整stderr缺失，C类环境因素仍待定。完整strict语义审阅及第二次停机完整归档仍未完成，所有执行处置继续未定案。

## CI 审核

f98977911c340ea887059bd8cf9da2ec10ddf162 的四项工作流已在远程核对为 completed/success，主 CI 并非仍在运行。该状态仅确认停机复核文档提交已通过检查。

四项工作流的完整提交号、运行ID、GitHub链接、创建/更新时间及状态已归档至[机器可读CI记录](../audits/scale100-f989779-ci-2026-11-26.json)。本审阅记录的后续提交和CI另行报告，不用它替代下表中的历史记录。

| 提交用途 | 提交 | CI | docs | Security | Plugin CI |
| --- | --- | --- | --- | --- | --- |
| 恢复代码 | 518d384 | 43589385469 | 43589385483 | 43589385653 | 43589385497 |
| 启动文档 | 1e6c5cc | 43590936691 | 43590936712 | 43590936935 | 43590936751 |
| 停机复核 | f989779 | 45754214763 | 45754214829 | 45754215032 | 45754214727 |
| 首份待批准审阅记录 | 494beb2 | 45757919562 | 45757919580 | 45757919865 | 45757919495 |

以上记录分开引用，不证明实验已完成。2026-11-26另行核对494beb2的四项工作流均为completed/success；本次三项调查/设计产出的新提交与CI仍另计。

## 定向证据审阅与候选处置

| 对象 | 已确认证据 | 候选处置及待决条件 |
| --- | --- | --- |
| Pylint-4661 gated | patch 42,071字符，与stats.diff_chars一致；stats身份与索引一致；单条prediction身份及补丁内容一致；trace/state存在；官方report缺失；CSV resolved为空 | 仅补评分是候选。须先确定补丁应用失败原因、审阅完整strict证据和评分契约；不修改补丁，不将其记作unresolved，不自动重采样Agent。当前不定案。 |
| Pylint-4661 baseline | 只有initial_artifacts，无stats、patch、prediction或report；容器日志有Agent活动，停机记录清理一个所属容器 | 中断后用新attempt重跑是候选。须先关联任务、容器和中断时点并获得恢复指示；缺失耗时不推算。当前不定案。 |
| Pylint-4661整对 | 尚无完整官方配对 | 是否从主P90排除须基于外部中断证据另行审阅。评分故障本身不自动构成排除官方解决率样本的理由；不能为了得到完整结果而静默缩减预注册样本。当前不定案。 |
| Django-11999 baseline | evidence_index选择原Agent标签和resume1评分标签；原patch/stats及新prediction/report存在 | 保留仅补评分路径；Agent已在首次停机前完成，不自动从成本排除。待收尾时做完整证据审阅。 |
| Django-12155 | evidence_index选择resume1的双组；patch/stats/prediction/report存在，原中断attempt保留 | 沿用已有外部中断证据与主P90整对排除，不将新attempt耗时说成失败尝试总成本。 |

Patch Apply Failed、harness_returned_none和grade_exit_3是已观察到的错误路径。底层原因未确定，没有证据表明Windows原子替换修复复发；杀毒、索引、IDE占用仍不得写成已证实原因。

## 各项审阅结论

| 项目 | 本轮审阅结论 | 批准执行前须补足 |
| --- | --- | --- |
| Pylint-4661 gated仅补评分 | 需补证，执行处置仍不定案 | 下节调查计划、完整strict证据审阅、评分契约及补丁失败归属确认；保存的补丁不变 |
| Pylint-4661 baseline新attempt | 需补证，执行处置仍不定案 | 任务—容器—中断时点的证据关联、真实停止原因及明确恢复指示 |
| Pylint-4661整对成本/样本 | 不定案 | 外部中断证据与成本有效性单独审阅；不得把成本排除变成删除预注册解决率样本 |
| Django-11999 | 维持原处理，待最终完整审阅 | 保留原Agent与resume1评分标签；不因评分中断自动排除Agent耗时 |
| Django-12155 | 维持原处理，待最终完整审阅 | 保留既定主P90整对排除、原失败attempt和新attempt；说明缺失的失败尝试总成本 |
| 恢复适配设计 | 作为未来实施需求提交，当前不实施 | 明确恢复指示、隔离适配、测试/模拟、公开CI及新计划一致性审核 |

这些结论不批准补评分、重跑、整对排除或自动生成正式成本文件。

## Pylint-4661只读根因调查清单

本节定义批准处置前的调查范围。本轮仅完成此前记录的定向身份/补丁一致性核对和脚本静态阅读；以下完整strict审阅、日志链关联和归因尚未全部完成。

1. 固定证据引用：以索引选择的Agent/评分标签关联原CSV行、stats、patch、prediction、评分命令与退出码、harness `run_instance.log`、初始工件与起点证明；为新增审阅产物记录来源哈希。只记录现有证据，不回写CSV、补丁或日志。
2. 区分补丁失败的阶段：先从完整harness日志辨明失败的是Agent补丁应用、官方测试补丁应用还是后续评测步骤。`Patch Apply Failed`与reversed/previously applied提示不能单独确定责任；在此之前不声称镜像脏、补丁错误或重复应用是根因。
3. 审阅46个diff文件的路径、hunk上下文、换行及编码，与保存的prediction和冻结起点证明逐项对应。必要的官方测试补丁对照仅在宿主私有审阅中进行，不向Agent暴露gold，也不把gold、问题正文或原始trace提交仓库。检查计划只读；本次不调用Docker、`git apply`、评分器或模型。
4. 完整strict证据审阅：关联trace每次阶段推进、拒绝及测试登记/移除声明与最终state、stats；核对测试保留政策、阶段与补丁的对应关系。`trace/state存在`及`test_preservation=strict`不等同于完整审阅通过。缺失或矛盾时保持阻断。
5. 评分契约确认：当前冻结`grade.py`在harness返回空结果时输出`PB_RESOLVED=0`并退出3，在正常返回分支才写公开report；批处理把异常退出标记为评分故障并清空resolved。因此当前缺report不能解释为官方unresolved。未来若需改变失败类型映射，必须先明确契约并另行实现、验证，不能直接改CSV结论。
6. baseline中断关联：从保存的启动/容器日志、label、进程及halt时间，把baseline任务与被清理的`0a8b80d3e343`建立可复核关联；当前只知道该容器在halt中被清理，尚不把容器归属视为已证明。若容器已不存在，使用保存证据并明确缺口，不重建运行来补造历史。
7. 输出待批准调查结论：明确已确认事实、未排除原因、尚缺证据及对候选处置的影响。仅补评分必须保留Agent补丁和原耗时；重跑必须有中断证据和新attempt；样本与成本分别审批。当前三项均不定案。

## 当前恢复工具的适用边界（未修改、未执行）

静态阅读发现两个需在未来恢复审阅中处理的限制：

1. scripts/scale100_classify_existing.py 的未完成任务分支要求 halt.reason 以 supervisor_error: 开头。当前 grade_infra_failure 不满足，因此现有分类器不会批准 Pylint baseline 直接重跑。不能改写 halt 来绕过条件。
2. scripts/scale100_recovery.py 为所有rerun固定记录 supervisor_permission_error_interrupted_agent。即使将来扩展分类器，仍须避免把第二次评分故障错误记为第一次PermissionError中断。

这只是代码适用范围审阅，尚未生成正式恢复计划。未来若获准恢复，需针对真实停机原因明确证据与成本映射，在隔离代码工作区完成适配、测试/模拟、公开CI及新计划的一致性审核；本次不改代码、不重启、不复用resume1。

## 未来恢复适配设计（提案，未实施）

### 分类与批准分离

保留`skip`、`regrade_only`、`rerun`、`pending`的调度语义，新增的审阅信息只作为设计提案：逐任务记录`observed_halt_reason`、证据引用/哈希、候选动作及审阅状态。`grade_infra_failure`描述观察到的故障，不是允许所有未完成任务重跑的授权。

完整可信patch/stats与缺失评分可产生`regrade_only`候选；有证据证明Agent被外部停机打断的任务可产生`rerun`候选。候选尚未批准、strict审阅不通过、官方证据矛盾、已完成但未知空补丁、容器归属不明时，计划保持`review_required`并阻止执行。成功评分项必须跳过；未知原因、仅靠note文本或缺少stats均不能单独授权重跑。

处置批准后才生成可执行计划，并绑定固定清单、索引、CSV、真实halt、证据哈希和批准范围。审批记录使用独立审阅产物，不改写原halt来适配旧条件。正式恢复前重新比对当前输入，任一证据变化都要求重新审阅。

### 中断原因与成本映射

将真实停止原因、任务受影响阶段及成本处置分别记录。保留原始`grade_infra_failure`，另标识“评分失败任务”与“被监督器停止的并行Agent”；不能把它们统称为PermissionError中断。

新的attempt中断账本须引用本次归档的真实halt及相应日志；与第一次PermissionError账本并存，按实例/模式/attempt/中断事件去重，不能覆盖`django-12155`既定记录。成本排除依据独立审阅状态，不由`rerun`动作或grade故障名称自动推导。当前不生成`cost_exclusions.json`。

### 证据与执行约束

旧评分故障CSV行、Agent输出、评分日志和索引历史须在新现场归档中保留。未来新attempt只能创建新标签；仅评分不得调用Agent；Agent标签与评分标签可不同，最终只取新索引明确选定的证据。有效已评分行禁止覆盖；故障行若经批准替换，必须先保留原行及来源，且不将两条结果计作两个预注册任务。

停止事件、候选/批准动作、选择证据和成本处理应分别可追溯。代码适配仅在隔离控制器工作区实施，原Agent、规则、样本、协议、模型参数及镜像/起点证明继续冻结。CI绿、设计通过或模拟通过均不能替代正式恢复指示。

### 拟覆盖的测试与模拟范围（当前未执行）

| 范围 | 拟验证条件 |
| --- | --- |
| 真实第二次halt | 从当前真实停机元数据派生脱敏fixture，保留来源哈希；grade故障不误归类为PermissionError，原halt不修改 |
| gated仅评分 | 身份、patch/prediction、strict审阅及批准范围一致才产生可执行动作；断言Agent调用为0，原patch/stats不变 |
| 并行baseline中断 | 任务/容器/时点证据与批准齐全时可选择新attempt；缺任一项、未知原因或清理失败则阻断 |
| 成本映射 | 评分中断不自动排除已完成Agent；并行Agent中断待独立审阅；两次中断账本并存且不重复，不硬编码PermissionError |
| 证据一致性 | 索引、CSV、report、prediction、stats、strict trace/state矛盾阻断；缺报告不计unresolved，未知空补丁不重采样 |
| 历史结果保护 | 98项已有官方评分保持不变；原23项、两次启动/attempt及原故障行历史保留；新标签不可复用resume1 |
| 计划门槛 | 归档/模拟/公开CI/批准范围/输入哈希任一缺失或变化均拒绝执行，拒绝时不写正式运行根 |
| 独立进程模拟 | 临时合成目录注入一次评分故障并中断并行Agent，真实进程停止留档；经模拟专用批准后恢复，验证仅评分、跳过已评分及混合标签证据；真实模型调用为0 |

上述测试只定义范围，不宣称已实现或通过。取得明确恢复指示后，先归档当前现场、审阅证据，再隔离实施与测试/模拟；推送适配并通过公开CI后，生成新恢复计划并重新做一致性审核，批准后才执行恢复。当前不运行模拟，不改代码。

## 准备表与异常清单

scale100-evidence-review-checklist-20261126.json 的200个键与固定清单及 evidence_index 完全对应；Agent/评分标签与当前索引一致，CSV note与现场一致。只核对映射，没有逐项审计200项内容或执行完成核验。

待审阅项目：

- 首次PermissionError及Django-12155中断；Django-11999仅评分中断与原Agent耗时。
- 第二次grade_infra_failure与Pylint-4661双组的处置及中断成本。
- Django-15268测试保留拒绝；门禁完成但官方未解决的Django-13212、Requests-1724。
- 完成后重新列出空补丁、证据不一致、零拦截、长耗时及其他异常；不提前认定当前列表为最终全量。
- 原23项评分及旧attempt保留；首次恢复模拟跳过已完成首项的验收与正式证据保留证明，不能互相替代。

resume1的14,414.25秒只是恢复批次日历跨度，含Agent、评分及调度；不与两组Agent耗时相加，不代表失败尝试总成本，不扣除猜测的未知墙钟耗时。

## 待执行的收尾参数（当前禁止执行）

| 参数 | 准备值 |
| --- | --- |
| 解释器 | D:/PyCharmMiscProject/phase-barrier/.venv/Scripts/python.exe |
| 脚本 | 隔离控制器工作区 scripts/scale100_analysis.py |
| --manifest | 原执行工作区 benchmarks/swebench/manifests/scale100-verified-2026-11-14.json |
| --run-root | D:/PyCharmMiscProject/phase-barrier/.pytest_tmp/scale100_20261114 |
| --source-root | D:/PyCharmMiscProject/phase-barrier |
| --cost-exclusions | 正式运行根内、在全部中断审阅后生成的cost_exclusions.json；当前尚未批准 |
| --bootstrap-reps | 10000 |
| --seed | 2026 |

退出顺序：明确恢复指示及处置 → 新现场归档/计划/模拟/发布CI门槛 → 全部200官方评分/100配对 → 证据与异常成本审阅 → 预注册统计及最终报告 → 最终CI → v1.2.1。当前停在只读处置范围草案。
