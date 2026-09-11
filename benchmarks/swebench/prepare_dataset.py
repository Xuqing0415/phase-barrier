"""SWE-bench 数据集准备（2026-09）：导出可复现的实例清单。

从 canonical SWE-bench 数据集（HuggingFace 名或本地 parquet/json/jsonl）导出实例行，并
可按「本机已缓存的官方评测镜像」过滤——这样导出的清单保证跑得起来，不会跑到一半缺镜像。

用法（在装了 ``swebench``/``datasets`` 的评分 venv 里执行）::

    # 本机已缓存镜像对应的 Verified 实例（canonical，含 eval_script）
    HF_ENDPOINT=https://hf-mirror.com python benchmarks/swebench/prepare_dataset.py \
        --dataset SWE-bench/SWE-bench_Verified --split test \
        --local-images-only --out-dir data/verified

输出：

- ``{out-dir}/dataset.json``：完整实例行（``grade.py`` / ``run_agent.py`` 用）
- ``{out-dir}/tasks.json``：``[{instance_id, repo}]``（``scripts/run_swebench_batch*.py`` 用）
- stdout 摘要：实例数、仓库分布、缺失镜像数

拿到 dataset.json 后，用 ``scripts/select_swe_tasks.py`` 做分层抽样（含 must-repo 配额）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path


def load_dataset(name: str, split: str) -> list[dict]:
    """加载数据集：本地文件直读，HF 名走 datasets（可用 HF_ENDPOINT 走镜像）。"""
    path = Path(name)
    if path.exists():
        if path.suffix == ".jsonl":
            return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return raw
        raise SystemExit(f"{name}: 期望 JSON 数组")
    from datasets import load_dataset as hf_load  # 延迟导入，便于无 HF 依赖时用本地文件

    return list(hf_load(name, split=split))


def image_for(instance: dict) -> str:
    """实例对应的官方评测镜像名：优先数据集 ``image`` 列，否则按官方命名规则推导。"""
    image = str(instance.get("image") or "").strip()
    if image:
        return image
    return ("swebench/sweb.eval.x86_64."
            + str(instance["instance_id"]).replace("__", "_1776_") + ":latest")


def local_images() -> set[str]:
    """本机 docker 已缓存的镜像名集合（含镜像站前缀的等价名）。"""
    try:
        proc = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                              capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        print("未找到 docker CLI；跳过本地镜像过滤", file=sys.stderr)
        return set()
    if proc.returncode != 0:
        print(f"docker images 失败：{proc.stderr.strip()}", file=sys.stderr)
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def has_local_image(image: str, cached: set[str]) -> bool:
    """官方名或任一镜像站前缀名命中即视为已缓存。"""
    if image in cached:
        return True
    repo, _, tag = image.partition(":")
    tag = tag or "latest"
    prefixes = ("docker.1panel.live/", "docker.1ms.run/", "docker.io/", "")
    return any(name == f"{prefix}{repo}:{tag}" for name in cached for prefix in prefixes)


def main() -> int:
    parser = argparse.ArgumentParser(description="SWE-bench 数据集准备 / 本地镜像过滤")
    parser.add_argument("--dataset", required=True, help="HF 数据集名，或本地 .json/.jsonl/.parquet")
    parser.add_argument("--split", default="test")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--local-images-only", action="store_true",
                        help="仅保留本机已缓存评测镜像的实例")
    parser.add_argument("--instance", nargs="*", default=[],
                        help="仅保留这些 instance_id（过滤后仍受 --local-images-only 约束）")
    parser.add_argument("--max", type=int, default=0, help="最多导出多少条（0 = 不限）")
    args = parser.parse_args()

    rows = load_dataset(args.dataset, args.split)
    print(f"数据集 {args.dataset}[{args.split}]：{len(rows)} 条")

    if args.instance:
        wanted = set(args.instance)
        rows = [row for row in rows if str(row.get("instance_id")) in wanted]

    missing: list[str] = []
    if args.local_images_only:
        cached = local_images()
        print(f"本机 docker 已缓存镜像：{len(cached)} 个 tag")
        kept = []
        for row in rows:
            image = image_for(row)
            if has_local_image(image, cached):
                kept.append(row)
            else:
                missing.append(str(row.get("instance_id")))
        rows = kept

    if args.max:
        rows = rows[: args.max]
    if not rows:
        print("过滤后没有可用实例（检查 --instance / --local-images-only）", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = args.out_dir / "dataset.json"
    dataset_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    tasks = [{"instance_id": str(row["instance_id"]), "repo": str(row.get("repo", ""))}
             for row in rows]
    tasks_path = args.out_dir / "tasks.json"
    tasks_path.write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"已写入 {dataset_path}（{len(rows)} 条）与 {tasks_path}")
    for repo, count in sorted(Counter(t["repo"] for t in tasks).items()):
        print(f"  {count:3d}  {repo}")
    if missing:
        print(f"本地缺镜像而跳过 {len(missing)} 条：{', '.join(sorted(missing)[:6])}"
              + (" ..." if len(missing) > 6 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
