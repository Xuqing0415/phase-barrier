"""批量拉取 SWE-bench 官方评测镜像（可从镜像站拉、再 tag 回官方名）。

动机：`benchmarks/swebench/prepare_dataset.py --local-images-only` 只能选本机已缓存的
实例；要扩样本就得先把镜像拉全。国内直连 Docker Hub 常失败，本脚本支持先拉镜像站
（如 `docker.1panel.live`）再 `docker tag` 成官方名，这样 `swebench` harness 不用改配置。

用法::

    python scripts/pull_swebench_images.py \
        --tasks .pytest_tmp/bench_data/verified_full/tasks_scale30.json \
        --mirror docker.1panel.live --concurrency 3

    # 只看看要拉哪些，不动网络
    python scripts/pull_swebench_images.py --tasks tasks.json --dry-run

退出码：全部可用为 0；有镜像失败为 1（失败清单打印在末尾，可重跑补齐）。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path


def _docker(*args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _images_in_tasks(tasks_path: Path) -> list[str]:
    raw = json.loads(tasks_path.read_text(encoding="utf-8"))
    rows = raw if isinstance(raw, list) else next(
        raw[key] for key in ("tasks", "instances", "data", "rows") if isinstance(raw.get(key), list)
    )
    images: list[str] = []
    for row in rows:
        image = str(row.get("image") or "").strip()
        if not image:
            instance_id = str(row.get("instance_id") or "").strip()
            repo, _, number = instance_id.partition("-")
            if not repo or not number:
                continue
            owner, _, name = repo.partition("__")
            image = f"swebench/sweb.eval.x86_64.{owner}_1776_{name}-{number}:latest"
        if not image.endswith(":latest"):
            image += ":latest"
        images.append(image)
    return sorted(set(images))


def _local_images() -> set[str]:
    proc = _docker("images", "--format", "{{.Repository}}:{{.Tag}}")
    if proc.returncode != 0:
        print(f"[warn] docker images 失败：{proc.stderr.strip()}", file=sys.stderr)
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def _pull_one(image: str, mirror: str) -> tuple[str, bool, str]:
    source = f"{mirror.rstrip('/')}/{image}" if mirror else image
    pull = _docker("pull", source)
    if pull.returncode != 0:
        return image, False, (pull.stderr or pull.stdout).strip().splitlines()[-1] if (pull.stderr or pull.stdout).strip() else "pull failed"
    if source != image:
        tag = _docker("tag", source, image)
        if tag.returncode != 0:
            return image, False, f"tag failed: {tag.stderr.strip()}"
    return image, True, "ok"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="批量拉取 / 打标 SWE-bench 官方评测镜像")
    parser.add_argument("--tasks", required=True, help="tasks.json（含 image 字段，缺失则按 instance_id 推导）")
    parser.add_argument("--mirror", default="docker.1panel.live", help="镜像站前缀（留空表示直连官方）")
    parser.add_argument("--concurrency", type=int, default=3, help="并发拉取数（默认 3）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划")
    args = parser.parse_args(argv)

    tasks_path = Path(args.tasks)
    if not tasks_path.exists():
        print(f"[error] 找不到 tasks 文件：{tasks_path}", file=sys.stderr)
        return 2
    wanted = _images_in_tasks(tasks_path)
    present = _local_images()
    missing = [image for image in wanted if image not in present]
    print(f"实例镜像 {len(wanted)} 个，已缓存 {len(wanted) - len(missing)} 个，待拉取 {len(missing)} 个")
    for image in missing:
        print(f"  - {image}")
    if args.dry_run or not missing:
        return 0

    failures: list[tuple[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = {pool.submit(_pull_one, image, args.mirror): image for image in missing}
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            image, ok, note = future.result()
            print(f"[{index}/{len(missing)}] {'OK  ' if ok else 'FAIL'} {image} {note}")
            if not ok:
                failures.append((image, note))

    if failures:
        print(f"\n失败 {len(failures)} 个（可重跑本命令补齐）：", file=sys.stderr)
        for image, note in failures:
            print(f"  - {image} :: {note}", file=sys.stderr)
        return 1
    print("\n全部镜像就绪。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
