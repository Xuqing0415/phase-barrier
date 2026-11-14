"""Readiness must distinguish absent tags from an inaccessible Docker daemon."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('check_prereg_images', ROOT / 'scripts/check_prereg_images.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def test_exact_tags_and_daemon_errors_are_not_missing(tmp_path, monkeypatch, capsys):
    tags = ['swebench/exact-a:latest', 'swebench/exact-b:latest', 'swebench/exact-c:latest']
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'count': 3, 'instances': [
        {'instance_id': str(i), 'image': tag} for i, tag in enumerate(tags)
    ]}), encoding='utf-8')
    responses = iter([
        subprocess.CompletedProcess([], 0, json.dumps([{
            "Id": "sha256:image-a", "Os": "linux", "Architecture": "amd64",
            "RootFS": {"Layers": ["sha256:layer-a"]},
        }]), ''),
        subprocess.CompletedProcess([], 1, '[]', 'Error response from daemon: No such image: exact-b'),
        subprocess.CompletedProcess([], 1, '', 'permission denied connecting to Docker API'),
    ])
    commands = []

    def inspect(command, **kwargs):
        commands.append(command)
        return next(responses)

    monkeypatch.setattr(checker.subprocess, 'run', inspect)
    history = tmp_path / 'history.jsonl'
    assert checker.main(['--manifest', str(manifest), '--output', str(history)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert commands == [['docker', 'image', 'inspect', '--platform', 'linux/amd64', tag] for tag in tags]
    assert report['ready'] == report['missing_count'] == len(report['errors']) == 1
    assert report['missing_images'][0]['instance_id'] == '1'
    assert report['errors'][0]['instance_id'] == '2'
    assert report['ready_images'][0]['image_id'] == 'sha256:image-a'
    assert report['ready_images'][0]['rootfs_layers'] == ['sha256:layer-a']
    assert json.loads(history.read_text(encoding='utf-8')) == report


@pytest.mark.parametrize('failure', [FileNotFoundError('docker missing'), subprocess.TimeoutExpired('docker', 20)])
def test_unavailable_cli_and_timeout_are_errors(tmp_path, monkeypatch, capsys, failure):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'instances': [{'instance_id': 'a', 'image': 'swebench/a:latest'}]}))

    def inspect(*args, **kwargs):
        raise failure

    monkeypatch.setattr(checker.subprocess, 'run', inspect)
    assert checker.main(['--manifest', str(manifest)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['ready'] == report['missing_count'] == 0
    assert len(report['errors']) == 1


@pytest.mark.parametrize('manifest', [
    [], {'instances': []}, {'count': 20, 'instances': [{'instance_id': 'a', 'image': 'swebench/a:latest'}]},
    {'instances': [{'instance_id': 'a', 'image': 'swebench/a:latest'}] * 2},
])
def test_invalid_manifest_is_rejected(tmp_path, manifest):
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError):
        checker._images(path)


def test_fixed_manifest_has_twenty_unique_exact_tags():
    images = checker._images(checker.DEFAULT_MANIFEST)
    assert len(images) == 20
    assert len({iid for iid, _ in images}) == len({tag for _, tag in images}) == 20
    assert all(tag.startswith('swebench/sweb.eval.x86_64.') for _, tag in images)


def test_scale_manifest_tasks_shape_is_accepted(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"count": 1, "tasks": [
        {"instance_id": "django__django-1", "image": "logicstar/django:latest"}
    ]}), encoding="utf-8")
    assert checker._images(path) == [("django__django-1", "logicstar/django:latest")]
