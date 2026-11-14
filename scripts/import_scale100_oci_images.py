"""Verify and import only the preregistered images from a frozen OCI layout."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(tag: str) -> str:
    if tag.startswith("docker.io/"):
        tag = tag[len("docker.io/"):]
    if ":" not in tag.rsplit("/", 1)[-1]:
        tag += ":latest"
    return tag


class Layout:
    def __init__(self, root: Path):
        self.root = root
        self.verified: set[str] = set()

    def blob(self, descriptor: dict) -> tuple[str, Path]:
        digest = str(descriptor.get("digest", ""))
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("invalid or unsupported OCI descriptor digest")
        relative = "blobs/sha256/" + digest.split(":", 1)[1]
        path = self.root / relative
        if not path.is_file() or path.stat().st_size != descriptor.get("size"):
            raise ValueError("missing or truncated OCI blob: " + digest)
        if relative not in self.verified:
            if sha256(path) != digest.split(":", 1)[1]:
                raise ValueError("OCI blob hash mismatch: " + digest)
            self.verified.add(relative)
        return relative, path

    def json_blob(self, descriptor: dict) -> tuple[dict, str, Path]:
        relative, path = self.blob(descriptor)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid OCI JSON blob: " + descriptor["digest"]) from exc
        return value, relative, path


def load_manifest(path: Path) -> tuple[list[dict], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks = payload.get("tasks") if isinstance(payload, dict) else None
    if not isinstance(tasks, list) or len(tasks) != payload.get("count"):
        raise ValueError("preregistered manifest task count is invalid")
    tags = [str(row.get("image", "")) for row in tasks]
    ids = [str(row.get("instance_id", "")) for row in tasks]
    if len(set(tags)) != len(tags) or len(set(ids)) != len(ids) or any(not x for x in tags + ids):
        raise ValueError("manifest contains empty or duplicate task IDs/image tags")
    return tasks, hashlib.sha256(path.read_bytes()).hexdigest()


def import_images(layout_dir: Path, manifest_path: Path, provenance_path: Path,
                  log_path: Path, *, docker: str = "docker") -> dict:
    tasks, manifest_sha = load_manifest(manifest_path)
    layout_root = layout_dir / "layout" if (layout_dir / "layout/index.json").exists() else layout_dir
    index = json.loads((layout_root / "index.json").read_text(encoding="utf-8"))
    wanted = {canonical(row["image"]): row["instance_id"] for row in tasks}
    selected: dict[str, dict] = {}
    oci = Layout(layout_root)
    for descriptor in index.get("manifests", []):
        aliases = descriptor.get("annotations", {})
        for key in ("org.opencontainers.image.ref.name", "io.containerd.image.name"):
            alias = aliases.get(key)
            if alias and canonical(str(alias)) in wanted:
                selected[canonical(str(alias))] = descriptor
    if set(selected) != set(wanted):
        missing = sorted(set(wanted) - set(selected))
        raise ValueError(f"OCI layout lacks {len(missing)} selected tags: {missing[:5]}")

    files: dict[str, Path] = {}
    docker_manifest: list[dict] = []
    provenance = []
    for tag, descriptor in sorted(selected.items()):
        manifest, _, _ = oci.json_blob(descriptor)
        config, config_rel, config_path = oci.json_blob(manifest["config"])
        if config.get("os") != "linux" or config.get("architecture") != "amd64":
            raise ValueError("selected image is not linux/amd64: " + tag)
        files[config_rel] = config_path
        layers = []
        for layer in manifest.get("layers", []):
            rel, path = oci.blob(layer)
            files[rel] = path
            layers.append(rel)
        docker_manifest.append({"Config": config_rel, "RepoTags": [tag], "Layers": layers})
        provenance.append({
            "instance_id": wanted[tag], "image": tag,
            "oci_manifest_digest": descriptor["digest"],
            "config_digest": manifest["config"]["digest"],
            "runtime_config": config.get("config", {}),
            "rootfs_diff_ids": config.get("rootfs", {}).get("diff_ids", []),
            "layer_digests": [item["digest"] for item in manifest.get("layers", [])],
        })

    # Refuse to overwrite a pre-existing selected tag.
    for tag in wanted:
        check = subprocess.run([docker, "image", "inspect", tag], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=30)
        if check.returncode == 0:
            raise ValueError("refusing to overwrite existing selected image tag: " + tag)
        if "No such image" not in check.stderr and "No such image" not in check.stdout:
            raise RuntimeError("Docker inspect failed before import: " + tag)

    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("wb")
    process = subprocess.Popen([docker, "load"], stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
    try:
        assert process.stdin is not None
        with tarfile.open(fileobj=process.stdin, mode="w|") as archive:
            data = json.dumps(docker_manifest, separators=(",", ":")).encode("utf-8")
            info = tarfile.TarInfo("manifest.json")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
            for name, path in files.items():
                info = tarfile.TarInfo(name)
                info.size = path.stat().st_size
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
        process.stdin.close()
        if process.wait(timeout=7200) != 0:
            raise RuntimeError("docker load failed; inspect the saved import log")
    except BaseException:
        if process.poll() is None:
            process.terminate()
        raise
    finally:
        log.close()

    def normalized(config: dict) -> dict:
        return {key: value for key, value in config.items() if value not in (None, "", [], {}, False)}

    for row in provenance:
        proc = subprocess.run([docker, "image", "inspect", "--platform", "linux/amd64", row["image"]],
                              check=True, capture_output=True, text=True, encoding="utf-8", timeout=60)
        actual = json.loads(proc.stdout)[0]
        if actual.get("Os") != "linux" or actual.get("Architecture") != "amd64":
            raise ValueError("imported platform mismatch: " + row["image"])
        if actual.get("RootFS", {}).get("Layers", []) != row["rootfs_diff_ids"]:
            raise ValueError("imported rootfs diff IDs mismatch: " + row["image"])
        if normalized(actual.get("Config", {})) != normalized(row["runtime_config"]):
            raise ValueError("imported runtime config mismatch: " + row["image"])
        row["docker_image_id"] = actual["Id"]
        row["content_verified"] = True
    result = {
        "manifest_sha256": manifest_sha,
        "oci_index_sha256": sha256(layout_root / "index.json"),
        "total": len(tasks), "ready": len(provenance), "errors": [],
        "unique_blobs": len(files), "images": provenance,
    }
    provenance_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    result = import_images(args.layout, args.manifest, args.provenance, args.log)
    print(json.dumps({key: result[key] for key in ("total", "ready", "errors", "unique_blobs")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
