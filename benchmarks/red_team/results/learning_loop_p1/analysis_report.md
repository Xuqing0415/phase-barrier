# 拦截案例分析报告

- 案例总数：6
- 分组数：2

| 防线 | 触发类型 | 案例数 | 新发现 |
|------|----------|--------|--------|
| behavior_audit | forbidden_op:file_delete | 5 | — |
| defense4 | escape:equivalent_op | 1 | 新规则候选 1 |

## defense4 / escape:equivalent_op

- `truncate -s` -> `(?i)\btruncate\s+[^\n;&|]{0,40}?\-s\b`（来自：`truncate -s 0 notes.txt
python -m pytest test_mod.py -q
# requirement.yaml
goal:`）
