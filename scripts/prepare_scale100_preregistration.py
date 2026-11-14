"""Create the public Scale-100 manifest and a host-only grading dataset."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.select_swe_tasks import (  # noqa: E402
    load_exclude_ids,
    load_rows,
    parse_repo_quotas,
    select_tasks_balanced,
)

PUBLIC_FIELDS = ("instance_id", "repo", "base_commit", "difficulty", "image")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(dataset: Path, exclusion: Path, manifest_out: Path, host_dataset_out: Path,
            *, count: int, seed: int, quotas: dict[str, int]) -> dict:
    dataset = dataset.resolve()
    exclusion = exclusion.resolve()
    rows = load_rows(dataset)
    excluded = load_exclude_ids(exclusion)
    chosen, meta = select_tasks_balanced(
        rows, count=count, seed=seed, repo_quotas=quotas, exclude=tuple(excluded)
    )
    ids = [str(row["instance_id"]) for row in chosen]
    if len(ids) != count or len(set(ids)) != count:
        raise ValueError(f"expected {count} distinct selected instances; got {len(ids)}")
    if set(ids) & set(excluded):
        raise ValueError("selection overlaps the historical exclusion set")
    selected_repos = Counter(str(row["repo"]) for row in chosen)
    if dict(selected_repos) != quotas:
        raise ValueError("selected repository counts do not match preregistered quotas")

    public_tasks = []
    host_rows = []
    for row in chosen:
        public = {key: row.get(key) for key in PUBLIC_FIELDS}
        if not all(public.get(key) for key in ("instance_id", "repo", "base_commit", "image")):
            raise ValueError(f"selected row lacks a required public field: {row['instance_id']}")
        image = str(row["image"])
        if image.startswith("swebench/"):
            image = "logicstar/" + image[len("swebench/"):]
        elif not image.startswith("logicstar/"):
            raise ValueError(f"unexpected image namespace for {row['instance_id']}: {image}")
        public["image"] = image
        public_tasks.append(public)
        host_row = dict(row)
        host_row["image"] = image
        host_rows.append(host_row)

    payload = {
        "schema_version": 1,
        "dataset": "SWE-bench/SWE-bench_Verified",
        "split": "test",
        "generated_at": "2026-11-14",
        "source_release": "v1.2.0",
        "source_commit": "db48bf87d3439d998dd9c3ce6abe9db202ead01f",
        "count": count,
        "seed": seed,
        "dataset_sha256": sha256(dataset),
        "exclusion_manifest": exclusion.relative_to(ROOT).as_posix(),
        "exclusion_manifest_sha256": sha256(exclusion),
        "historical_excluded_count": len(set(excluded)),
        "eligible_pool_count": len(rows) - len(set(excluded) & {str(r["instance_id"]) for r in rows}),
        "selection": {
            "method": "fixed repository quotas with deterministic within-repository difficulty rotation",
            "quota_mode": meta["quota_mode"],
            "repo_quotas": dict(sorted(quotas.items())),
            "repo_counts": dict(sorted(selected_repos.items())),
            "difficulty_distribution": meta["difficulty_selected"],
            "difficulty_available": meta["difficulty_available"],
        },
        "tasks": public_tasks,
    }
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    host_dataset_out.parent.mkdir(parents=True, exist_ok=True)
    manifest_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    host_dataset_out.write_text(json.dumps(host_rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {
        "manifest": str(manifest_out),
        "host_dataset": str(host_dataset_out),
        "count": count,
        "repo_counts": dict(sorted(selected_repos.items())),
        "difficulty_distribution": meta["difficulty_selected"],
        "dataset_sha256": payload["dataset_sha256"],
        "manifest_sha256": sha256(manifest_out),
        "host_dataset_sha256": sha256(host_dataset_out),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--exclusion", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path, required=True)
    parser.add_argument("--host-dataset-out", type=Path, required=True)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--repo-quota", nargs="+", required=True, metavar="REPO=COUNT")
    args = parser.parse_args(argv)
    try:
        quotas = parse_repo_quotas(args.repo_quota)
        summary = prepare(
            args.dataset, args.exclusion, args.manifest_out, args.host_dataset_out,
            count=args.count, seed=args.seed, quotas=quotas,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
