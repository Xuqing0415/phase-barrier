"""Fail closed on missing or altered frozen OCI blobs."""
import hashlib
import json

import pytest

from scripts.import_scale100_oci_images import Layout, canonical, import_images


def test_blob_checks_size_digest_and_content(tmp_path):
    content = b'{"architecture":"amd64"}'
    digest = hashlib.sha256(content).hexdigest()
    path = tmp_path / "blobs" / "sha256" / digest
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    descriptor = {"digest": "sha256:" + digest, "size": len(content)}
    assert Layout(tmp_path).json_blob(descriptor)[0] == {"architecture": "amd64"}
    path.write_bytes(b"x" * len(content))
    with pytest.raises(ValueError, match="hash mismatch"):
        Layout(tmp_path).blob(descriptor)
    with pytest.raises(ValueError, match="invalid or unsupported"):
        Layout(tmp_path).blob({"digest": "../../secret", "size": 0})


def test_missing_tag_rejected_before_docker_is_called(tmp_path, monkeypatch):
    (tmp_path / "index.json").write_text(json.dumps({"manifests": []}), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"count": 1, "tasks": [
        {"instance_id": "a__a-1", "image": "logicstar/a:latest"}
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="lacks 1 selected tags"):
        import_images(tmp_path, manifest, tmp_path / "proof.json", tmp_path / "load.log")


def test_canonical_tag_preserves_explicit_version():
    assert canonical("docker.io/logicstar/example") == "logicstar/example:latest"
    assert canonical("logicstar/example:v1") == "logicstar/example:v1"
