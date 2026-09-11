# 拦截案例分析报告

- 案例总数：20
- 分组数：8

| 防线 | 触发类型 | 案例数 | 新发现 |
|------|----------|--------|--------|
| behavior_audit | forbidden_op:file_delete | 10 | — |
| behavior_audit | untraced_write | 1 | — |
| defense4 | escape:equivalent_op | 1 | 新规则候选 1 |
| defense5 | escape:risk_camouflage | 2 | — |
| dual_review | semantic_unavailable | 1 | — |
| formal_check | static_contradiction | 1 | 变量 1 |
| human_review | risk_sampling | 3 | — |
| requirement_template | vague_template | 1 | 空话短语 3 |

## defense4 / escape:equivalent_op

- `truncate -s` -> `(?i)\btruncate\s+[^\n;&|]{0,40}?\-s\b`（来自：`truncate -s 0 notes.txt
python -m pytest test_mod.py -q
# requirement.yaml
goal:`）
