"""SWE-bench 任务实例分层选择脚本（2026-09）。

从官方同构数据集（JSON 数组，含 ``instance_id`` / ``repo`` / ``difficulty`` 等列）中按仓库分层
抽样，输出可直接交给 ``scripts/run_swebench_batch*.py`` 的任务清单。特性：

- 按 ``repo`` 分组轮询，最大化仓库多样性；可指定必须覆盖的仓库（--must-repo）。
- 仅保留数据完整的实例（patch / base_commit 齐全），可排除已知数据质量问题实例（--exclude）。
- 组内优先混排 difficulty（easy / medium / hard），无该列时按 instance_id 稳定排序。
- 固定随机种子，结果可复现。
- ``--balance-difficulty``（默认关闭）：按 largest-remainder 给每个仓库分配配额
  （每仓至少 1 个），再在仓库内按难度等级轮转。输出带 ``selection_meta``，
  记录每仓配额、实际选中数与难度分布。关闭时行为与旧版完全一致。
- ``--repo-quota REPO=COUNT``：显式固定每个仓库的预注册配额，必须覆盖全部输入仓库
  且总和等于 ``--count``；此时不再使用 largest-remainder。
- ``--exclude-file``：从文本或 JSON 文件读取前序批次实例，记录在
  ``selection_meta.pre_excluded``。

用法::

    # dev 分片全部 6 个仓库中抽 12 个
    python scripts/select_swe_tasks.py --dataset swebench_dev.json --count 12 --out tasks.json

    # Lite test（含 django/sympy），强制覆盖 django 与 sympy，各 2 个起
    python scripts/select_swe_tasks.py --dataset swebench_test.json --count 20 \
        --must-repo django/django sympy/sympy --per-repo 2 --out tasks.json

    # Verified：12 个仓库分层 + 难度均衡，抽 15 个
    python scripts/select_swe_tasks.py --dataset verified.json --count 15 \
        --balance-difficulty --seed 7 --out verified_15.json

    # 预注册固定配额（可复现，不按镜像可用性换样本）
    python scripts/select_swe_tasks.py --dataset verified.json --count 20 \
        --repo-quota django/django=4 sympy/sympy=3 ... --exclude-file prior.txt \
        --out verified_20.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

DIFFICULTY_RANK = {"easy": 0, "medium": 1, "hard": 2}

#: 仅 --balance-difficulty 使用。Verified 的 difficulty 是时间桶，不是 easy/medium/hard；
#: 按预估耗时升序映射，未知值排最后。easy/medium/hard 与时间桶并存时视为同一等级。
BALANCE_DIFFICULTY_ORDER = {
    "easy": 0,
    "<15 min fix": 0,
    "medium": 1,
    "15 min - 1 hour": 1,
    "hard": 2,
    "1-4 hours": 2,
    ">4 hours": 3,
}


def load_rows(path: Path) -> list[dict]:
    """读取 JSON 数组或 {tasks|instances|data|rows: [...]} 包装，返回行列表。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("tasks", "instances", "data", "rows"):
            if isinstance(raw.get(key), list):
                return raw[key]
    raise ValueError(f"{path}: 无法识别为 JSON 数组或 tasks/instances/data/rows 包装")


def load_exclude_ids(path: Path) -> list[str]:
    """读取排除清单：JSON 字符串数组，或每行一个 instance_id（支持 # 注释）。"""
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return []
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return [
            line.split("#", 1)[0].strip()
            for line in text.splitlines()
            if line.split("#", 1)[0].strip()
        ]
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return [str(item) for item in raw]
    raise ValueError(f"{path}: 排除清单须为 JSON 字符串数组或每行一个 instance_id")


def parse_repo_quotas(items: list[str]) -> dict[str, int]:
    """解析 ``--repo-quota`` 的 ``REPO=COUNT`` 形式。"""
    quotas: dict[str, int] = {}
    for item in items:
        repo, sep, raw = item.rpartition("=")
        if not sep or not repo or not raw:
            raise ValueError(f"无效的仓库配额：{item!r}，应为 REPO=COUNT")
        try:
            count = int(raw)
        except ValueError as exc:
            raise ValueError(f"无效的仓库配额：{item!r}，COUNT 必须是整数") from exc
        if count < 0:
            raise ValueError(f"无效的仓库配额：{item!r}，COUNT 不能为负")
        quotas[repo] = count
    return quotas


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


def _filter_rows(
    rows: list[dict],
    include_repos: tuple[str, ...],
    exclude: tuple[str, ...],
) -> list[dict]:
    cleaned = clean_rows(rows)
    if include_repos:
        wanted = {r for r in include_repos}
        cleaned = [r for r in cleaned if r["repo"] in wanted]
    excluded = set(exclude)
    cleaned = [r for r in cleaned if r["instance_id"] not in excluded]
    if not cleaned:
        raise ValueError("筛选后没有可用实例（检查 include/exclude 与数据集）")
    return cleaned


def difficulty_level(row: dict) -> int:
    """把 difficulty 归一成可比较等级（仅均衡模式使用）。"""
    return BALANCE_DIFFICULTY_ORDER.get(str(row.get("difficulty", "")).strip().lower(), 99)


def quota_by_repo(pools: dict[str, list[dict]], count: int) -> dict[str, int]:
    """largest-remainder 配额：每个 repo 至少 1 个，余量按剩余容量比例分配。

    ``count`` 小于仓库数时无法满足「每仓至少 1」，按池大小降序、仓库名升序确定性地
    选前 ``count`` 个仓库。``count`` 超过数据总量时配额封顶到各池大小。
    """
    repos = sorted(pools)
    if not repos:
        return {}
    if count <= 0:
        return {r: 0 for r in repos}
    if count < len(repos):
        order = sorted(repos, key=lambda r: (-len(pools[r]), r))
        keep = set(order[:count])
        return {r: 1 if r in keep else 0 for r in repos}

    quota = {r: 1 for r in repos}
    capacity = {r: max(0, len(pools[r]) - 1) for r in repos}
    total_capacity = sum(capacity.values())
    extra = min(count - len(repos), total_capacity)
    if extra <= 0:
        return quota

    shares = {r: extra * capacity[r] / total_capacity for r in repos}
    for r in repos:
        quota[r] += int(shares[r])

    left = count - sum(quota.values())
    if left > 0:
        remainders = sorted(
            ((shares[r] - int(shares[r]), r) for r in repos if quota[r] < len(pools[r])),
            key=lambda item: (-item[0], item[1]),
        )
        for _fraction, r in remainders:
            if left <= 0:
                break
            quota[r] += 1
            left -= 1

    # 数据不足时按容量从大到小补满（确定性），不会超过各池大小。
    if left > 0:
        for r in sorted(repos, key=lambda r: (-len(pools[r]), r)):
            while left > 0 and quota[r] < len(pools[r]):
                quota[r] += 1
                left -= 1
            if left <= 0:
                break
    return quota


def rotate_by_difficulty(
    rows: list[dict],
    quota: int,
    *,
    seed: int = 7,
    repo: str = "",
) -> list[dict]:
    """在同一 repo 内按难度等级轮转：easy -> medium -> hard -> easy ...。

    每个等级内部用 ``seed + repo`` 做确定性打乱；某等级缺失时自动跳过。
    """
    if quota <= 0:
        return []
    buckets: dict[int, list[dict]] = {}
    for row in rows:
        buckets.setdefault(difficulty_level(row), []).append(row)
    levels = sorted(buckets)
    rng = random.Random(f"{seed}:{repo}")
    for level in levels:
        rng.shuffle(buckets[level])

    # 起始难度按 seed+repo 稳定偏移，避免所有仓库都从 easy 开始，导致聚合分布
    # 在「每仓 1 个」时机械地偏向 easy。hashlib 保证跨进程、跨平台可复现。
    if levels:
        digest = hashlib.sha256(f"{seed}:{repo}".encode("utf-8")).digest()
        start = int.from_bytes(digest[:4], "big") % len(levels)
        levels = levels[start:] + levels[:start]

    picked: list[dict] = []
    cursor = 0
    while len(picked) < quota:
        advanced = False
        for level in levels:
            bucket = buckets[level]
            if cursor < len(bucket):
                picked.append(bucket[cursor])
                advanced = True
                if len(picked) >= quota:
                    break
        if not advanced:
            break
        cursor += 1
    return picked


def select_tasks_balanced(
    rows: list[dict],
    count: int = 12,
    must_repos: tuple[str, ...] = (),
    per_repo: int = 0,
    include_repos: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    seed: int = 7,
    repo_quotas: dict[str, int] | None = None,
) -> tuple[list[dict], dict]:
    """仓库分层 + 配额 + 难度轮转，返回 (选中行, selection_meta)。

    ``repo_quotas`` 为显式预注册配额时，必须覆盖输入中的全部仓库且总和等于
    ``count``；此时不再使用 largest-remainder，也不调整 must-repo 配额。
    """
    cleaned = _filter_rows(rows, include_repos, exclude)
    by_repo: dict[str, list[dict]] = {}
    for row in cleaned:
        by_repo.setdefault(row["repo"], []).append(row)
    pools = {
        repo: sorted(repo_rows, key=lambda r: str(r["instance_id"]))
        for repo, repo_rows in by_repo.items()
    }
    if repo_quotas is not None:
        unknown = sorted(set(repo_quotas) - set(pools))
        missing = sorted(set(pools) - set(repo_quotas))
        if unknown:
            raise ValueError(f"显式配额包含未知仓库：{', '.join(unknown)}")
        if missing:
            raise ValueError(f"显式配额缺少仓库：{', '.join(missing)}")
        quota = {repo: int(repo_quotas[repo]) for repo in sorted(pools)}
        if sum(quota.values()) != count:
            raise ValueError(
                f"显式配额总和 {sum(quota.values())} != --count {count}"
            )
        over = [repo for repo, n in quota.items() if n > len(pools[repo])]
        if over:
            raise ValueError(
                "显式配额超过可用实例数："
                + ", ".join(f"{repo}={quota[repo]}/{len(pools[repo])}" for repo in over)
            )
        quota_mode = "explicit"
    else:
        quota = quota_by_repo(pools, count)
        quota_mode = "largest_remainder"

    # must-repo 是下限；缺口从配额 >1 的仓库按配额降序、仓库名升序让位。
    need = max(per_repo, 1) if per_repo else 1
    if repo_quotas is not None:
        for repo in must_repos:
            if quota.get(repo, 0) < need:
                raise ValueError(
                    f"显式配额 {repo}={quota.get(repo, 0)} 低于 must-repo 下限 {need}"
                )
    else:
        for repo in must_repos:
            if repo not in quota:
                continue
            while quota[repo] < need:
                donors = sorted(
                    (r for r in pools if r != repo and quota[r] > 1),
                    key=lambda r: (-quota[r], r),
                )
                if not donors:
                    break
                quota[donors[0]] -= 1
                quota[repo] += 1

    chosen: list[dict] = []
    meta_repos: dict[str, dict] = {}
    for repo in sorted(pools):
        selected = rotate_by_difficulty(
            pools[repo], quota[repo], seed=seed, repo=repo
        )
        chosen.extend(selected)
        available = Counter(str(r.get("difficulty") or "-") for r in pools[repo])
        picked = Counter(str(r.get("difficulty") or "-") for r in selected)
        note = ""
        if quota[repo] > len(available):
            note = (
                f"only {len(available)} difficulty bucket(s) for quota {quota[repo]}"
            )
        meta_repos[repo] = {
            "available": len(pools[repo]),
            "quota": quota[repo],
            "selected": len(selected),
            "difficulty_available": dict(sorted(available.items())),
            "difficulty_selected": dict(sorted(picked.items())),
            "balance_note": note,
        }

    chosen.sort(key=lambda r: (str(r["repo"]), str(r["instance_id"])))
    selected_ids = {str(r.get("instance_id")) for r in chosen}
    excluded = [
        {
            "instance_id": str(row.get("instance_id")),
            "repo": str(row.get("repo")),
            "difficulty": str(row.get("difficulty") or "-"),
            "reason": "not_selected_by_quota",
        }
        for repo in sorted(pools)
        for row in pools[repo]
        if str(row.get("instance_id")) not in selected_ids
    ]
    warnings: list[str] = []
    if len(chosen) < count:
        warnings.append(f"only {len(chosen)}/{count} selected (dataset exhausted)")

    available_total = Counter(str(r.get("difficulty") or "-") for r in cleaned)
    selected_total = Counter(str(r.get("difficulty") or "-") for r in chosen)
    meta = {
        "count_requested": count,
        "count_selected": len(chosen),
        "balance_difficulty": True,
        "quota_mode": quota_mode,
        "repo_quotas_requested": (
            dict(sorted(repo_quotas.items())) if repo_quotas is not None else None
        ),
        "seed": seed,
        "repos": meta_repos,
        "difficulty_available": dict(sorted(available_total.items())),
        "difficulty_selected": dict(sorted(selected_total.items())),
        "difficulty_distribution": dict(sorted(selected_total.items())),
        "excluded": excluded,
        "pre_excluded": [
            {"instance_id": iid, "reason": "excluded_by_caller"}
            for iid in sorted(set(exclude))
        ],
        "warnings": warnings,
    }
    return chosen, meta


def select_tasks(
    rows: list[dict],
    count: int = 12,
    must_repos: tuple[str, ...] = (),
    per_repo: int = 0,
    include_repos: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    seed: int = 7,
    balance_difficulty: bool = False,
    repo_quotas: dict[str, int] | None = None,
) -> list[dict]:
    """分层选择 count 个实例。must_repos 每个至少 per_repo 个（per_repo=0 时至少 1 个）。

    ``balance_difficulty=False`` 时保持旧行为；为 True 时委托给
    :func:`select_tasks_balanced`（只返回选中行，元数据请直接调用后者）。
    """
    if balance_difficulty or repo_quotas is not None:
        return select_tasks_balanced(
            rows,
            count=count,
            must_repos=must_repos,
            per_repo=per_repo,
            include_repos=include_repos,
            exclude=exclude,
            seed=seed,
            repo_quotas=repo_quotas,
        )[0]

    cleaned = _filter_rows(rows, include_repos, exclude)

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SWE-bench 任务分层抽样")
    ap.add_argument("--dataset", required=True, type=Path)
    ap.add_argument("--count", type=int, default=12)
    ap.add_argument("--must-repo", nargs="*", default=[])
    ap.add_argument("--per-repo", type=int, default=0,
                    help="must-repo 每个最少条数（默认 1）")
    ap.add_argument("--include-repo", nargs="*", default=[],
                    help="仅从这些仓库选（默认全部）")
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--exclude-file", type=Path, default=None,
                    help="排除清单文件（JSON 字符串数组或每行一个 instance_id）")
    ap.add_argument("--repo-quota", nargs="*", default=[], metavar="REPO=COUNT",
                    help="显式预注册配额；使用后自动进入平衡选择模式")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--balance-difficulty", action="store_true",
                    help="仓库分层 + largest-remainder 配额 + 难度轮转（默认关闭）")
    ap.add_argument("--out", type=Path, default=Path("tasks.json"))
    ap.add_argument("--json", dest="as_json", action="store_true",
                    help="仅打印所选 instance_id 的 JSON 数组")
    args = ap.parse_args(argv)

    rows = load_rows(args.dataset)
    try:
        exclude = list(args.exclude)
        if args.exclude_file:
            exclude += load_exclude_ids(args.exclude_file)
        repo_quotas = parse_repo_quotas(args.repo_quota) if args.repo_quota else None
    except ValueError as exc:
        ap.error(str(exc))
    balanced = args.balance_difficulty or repo_quotas is not None
    meta = None
    if balanced:
        chosen, meta = select_tasks_balanced(
            rows,
            count=args.count,
            must_repos=tuple(args.must_repo),
            per_repo=args.per_repo,
            include_repos=tuple(args.include_repo),
            exclude=tuple(exclude),
            seed=args.seed,
            repo_quotas=repo_quotas,
        )
    else:
        chosen = select_tasks(
            rows,
            count=args.count,
            must_repos=tuple(args.must_repo),
            per_repo=args.per_repo,
            include_repos=tuple(args.include_repo),
            exclude=tuple(exclude),
            seed=args.seed,
        )

    if args.as_json:
        print(json.dumps([r["instance_id"] for r in chosen], ensure_ascii=False))
        return 0

    payload = {"tasks": chosen, "selection_meta": meta} if meta is not None else chosen
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    counter = Counter(r["repo"] for r in chosen)
    print(f"已写入 {args.out}：{len(chosen)} 个实例")
    for repo, n in sorted(counter.items()):
        print(f"  {n:3d}  {repo}")
    for row in chosen:
        diff = row.get("difficulty") or "-"
        print(f"  - {row['instance_id']}  [{diff}]")
    if meta is not None:
        print("selection_meta:")
        for repo, info in meta["repos"].items():
            print(
                f"  {repo}: quota={info['quota']} selected={info['selected']} "
                f"diff={info['difficulty_selected']}"
                + (f" note={info['balance_note']}" if info["balance_note"] else "")
            )
        print(f"  难度总数：{meta['difficulty_selected']}")
        for warning in meta["warnings"]:
            print(f"  警告：{warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
