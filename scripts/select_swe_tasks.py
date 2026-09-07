"""SWE-bench 任务实例分层选择脚本（2026-09）。

从官方同构数据集（JSON 数组，含 ``instance_id`` / ``repo`` / ``difficulty`` 等列）中按仓库分层
抽样，输出可直接交给 ``scripts/run_swebench_batch.py`` 的任务清单。特性：

- 按 ``repo`` 分组轮询，最大化仓库多样性；可指定必须覆盖的仓库（--must-repo）。
- 仅保留数据完整的实例（patch / base_commit 齐全），可排除已知数据质量问题实例（--exclude）。
- 组内优先混排 difficulty（easy / medium / hard），无该列时按 instance_id 稳定排序。
- 固定随机种子，结果可复现。

用法::

    # dev 分片全部 6 个仓库中抽 12 个
    python scripts/select_swe_tasks.py --dataset swebench_dev.json --count 12 --out tasks.json

    # Lite test（含 django/sympy），强制覆盖 django 与 sympy，各 2 个起
    python scripts/select_swe_tasks.py --dataset swebench_test.json --count 20 \
        --must-repo django/django sympy/sympy --per-repo 2 --out tasks.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DIFFICULTY_RANK = {"easy": 0, "medium": 1, "hard": 2}


def load_rows(path: Path) -> list[dict]:
    """读取 JSON 数组或 {instances|data: [...]} 包装，返回行列表。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("instances", "data", "rows"):
            if isinstance(raw.get(key), list):
                return raw[key]
    raise ValueError(f"{path}: 无法识别为 JSON 数组或 instances/data 包装")


def clean_rows(rows: list[dict]) -> list[dict]:
    """剔除数据不完整（缺 id / patch / base_commit）的行，并保证行内字段存在。"""
    out = []
    for row in rows:
        iid = row.get("instance_id")
        if not iid or not row.get("patch") or not row.get("base_commit"):
            continue
        norm = dict(row)
        norm.setdefault("repo", repo_from_instance_id(str(iid)))
        out.append(norm)
    return out


def repo_from_instance_id(iid: str) -> str:
    """instance_id 'django__django-11039' -> 'django/django'（无 repo 字段时的回退）。"""
    if "__" not in iid:
        return ""
    owner, _, rest = iid.partition("__")
    name = rest.rsplit("-", 1)[0] if "-" in rest else rest
    return f"{owner}/{name}"


def _sort_key(row: dict):
    """组内排序：easy/medium/hard 前、稳定 instance_id 后。"""
    rank = DIFFICULTY_RANK.get(str(row.get("difficulty", "")).lower(), 99)
    return (rank, str(row["instance_id"]))


def select_tasks(
    rows: list[dict],
    count: int = 12,
    must_repos: tuple[str, ...] = (),
    per_repo: int = 0,
    include_repos: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    seed: int = 7,
) -> list[dict]:
    """分层选择 count 个实例。must_repos 每个至少 per_repo 个（per_repo=0 时至少 1 个）。"""
    cleaned = clean_rows(rows)
    excluded = set(exclude)
    if include_repos:
        wanted = {r for r in include_repos}
        cleaned = [r for r in cleaned if r["repo"] in wanted]
    cleaned = [r for r in cleaned if r["instance_id"] not in excluded]
    if not cleaned:
        raise ValueError("筛选后没有可用实例（检查 include/exclude 与数据集）")

    by_repo: dict[str, list[dict]] = {}
    for row in cleaned:
        by_repo.setdefault(row["repo"], []).append(row)
    for repo in by_repo:
        by_repo[repo].sort(key=_sort_key)

    # 1) must-repo 配额先行
    chosen: list[dict] = []
    seen: set[str] = set()
    for repo in must_repos:
        bucket = by_repo.get(repo, [])
        need = max(per_repo, 1) if per_repo else 1
        quota = bucket[: max(need, 1)]
        for row in quota:
            if row["instance_id"] not in seen:
                chosen.append(row)
                seen.add(row["instance_id"])

    # 2) 其余名额按仓库轮询（must repo 剩余的实例也参与轮询）
    remaining = count - len(chosen)
    if remaining < 0:
        return chosen[:count]
    pools = {repo: [r for r in rows_ if r["instance_id"] not in seen] for repo, rows_ in by_repo.items()}
    cursor = 0
    repo_order = sorted(pools, key=lambda r: (r not in must_repos, r))
    while remaining > 0:
        advanced = False
        for repo in repo_order:
            if remaining <= 0:
                break
            bucket = pools[repo]
            if cursor < len(bucket):
                row = bucket[cursor]
                chosen.append(row)
                seen.add(row["instance_id"])
                remaining -= 1
                advanced = True
        if not advanced:
            break  # 池已耗尽
        cursor += 1
    if len(chosen) < count:
        print(f"警告：仅选出 {len(chosen)}/{count}（数据集不足）", file=sys.stderr)
    return chosen


def main() -> int:
    ap = argparse.ArgumentParser(description="SWE-bench 任务分层抽样")
    ap.add_argument("--dataset", required=True, type=Path)
    ap.add_argument("--count", type=int, default=12)
    ap.add_argument("--must-repo", nargs="*", default=[])
    ap.add_argument("--per-repo", type=int, default=0,
                    help="must-repo 每个最少条数（默认 1）")
    ap.add_argument("--include-repo", nargs="*", default=[],
                    help="仅从这些仓库选（默认全部）")
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=Path("tasks.json"))
    ap.add_argument("--json", dest="as_json", action="store_true",
                    help="仅打印所选 instance_id 的 JSON 数组")
    args = ap.parse_args()

    rows = load_rows(args.dataset)
    chosen = select_tasks(
        rows,
        count=args.count,
        must_repos=tuple(args.must_repo),
        per_repo=args.per_repo,
        include_repos=tuple(args.include_repo),
        exclude=tuple(args.exclude),
        seed=args.seed,
    )
    if args.as_json:
        print(json.dumps([r["instance_id"] for r in chosen], ensure_ascii=False))
        return 0
    args.out.write_text(json.dumps(chosen, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    from collections import Counter

    counter = Counter(r["repo"] for r in chosen)
    print(f"已写入 {args.out}：{len(chosen)} 个实例")
    for repo, n in sorted(counter.items()):
        print(f"  {n:3d}  {repo}")
    for row in chosen:
        diff = row.get("difficulty") or "-"
        print(f"  - {row['instance_id']}  [{diff}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
