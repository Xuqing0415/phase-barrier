"""Check local readiness of the preregistered SWE-bench image tags."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / Path(
    "benchmarks/swebench/manifests/verified20-preregistered-2026-11-08.json"
)


def _images(manifest_path: Path) -> list[tuple[str, str]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    rows = manifest.get("instances", manifest.get("tasks"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("manifest must contain a nonempty instances array")
    if manifest.get("count", len(rows)) != len(rows):
        raise ValueError("manifest count does not match instances")

    images: list[tuple[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("every instance must be an object")
        instance_id = str(row.get("instance_id") or "").strip()
        image = str(row.get("image") or "").strip()
        if not instance_id or not image:
            raise ValueError("every task must contain instance_id and image")
        images.append((instance_id, image))
    if len({iid for iid, _ in images}) != len(images):
        raise ValueError("manifest contains duplicate instance IDs")
    if len({image for _, image in images}) != len(images):
        raise ValueError("manifest contains duplicate image tags")
    return images


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--output", type=Path, help="append the JSON snapshot to this JSONL file")
    parser.add_argument("--expect-ready", type=int, help="fail unless exactly this many images are ready")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    try:
        images = _images(args.manifest)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 2

    ready: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for instance_id, image in images:
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", "--platform", "linux/amd64", image],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=args.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append({"instance_id": instance_id, "image": image, "error": str(exc)})
            continue

        item = {"instance_id": instance_id, "image": image}
        if result.returncode == 0:
            try:
                inspected = json.loads(result.stdout)
                if len(inspected) != 1:
                    raise ValueError("inspect must return exactly one image")
                image_info = inspected[0]
                platform = f"{image_info.get('Os', '')}/{image_info.get('Architecture', '')}"
                if platform != "linux/amd64":
                    errors.append({**item, "error": f"unexpected platform: {platform}"})
                    continue
                ready.append({
                    **item,
                    "image_id": image_info.get("Id"),
                    "platform": platform,
                    "rootfs_layers": image_info.get("RootFS", {}).get("Layers", []),
                })
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                errors.append({**item, "error": f"invalid inspect response: {exc}"})
        elif "No such image" in result.stderr or "No such image" in result.stdout:
            missing.append(item)
        else:
            errors.append(
                {
                    **item,
                    "error": (result.stderr or result.stdout).strip() or "inspect failed",
                }
            )

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(args.manifest),
        "ready": len(ready),
        "missing_count": len(missing),
        "total": len(images),
        "ready_images": ready,
        "missing_images": missing,
        "errors": errors,
    }
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("a", encoding="utf-8") as output_file:
            output_file.write(json.dumps(report) + "\n")
    return 1 if errors or (args.expect_ready is not None and len(ready) != args.expect_ready) else 0


if __name__ == "__main__":
    sys.exit(main())
