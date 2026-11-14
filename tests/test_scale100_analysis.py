"""Statistical primitives for the preregistered Scale-100 analysis."""
from scripts.scale100_analysis import analyze, clopper_pearson, exact_mcnemar, percentile
from scripts.prepare_scale100_preregistration import prepare
import json
from pathlib import Path
import math


def test_exact_mcnemar_reference_counts():
    assert exact_mcnemar(8, 2) == 0.109375
    assert exact_mcnemar(9, 1) == 0.021484375
    assert exact_mcnemar(0, 0) == 1.0


def test_conditional_exact_interval_is_bounded_and_ordered():
    low, high = clopper_pearson(9, 10)
    assert 0.5 < low < 0.9
    assert 0.9 < high < 1
    lo, hi = clopper_pearson(0, 10)
    assert lo == 0
    assert math.isclose(hi, 1 - 0.025**0.1, rel_tol=1e-12)


def test_linear_percentile_interpolates():
    assert percentile([0.0, 10.0], 0.9) == 9.0


def test_cost_bootstrap_is_paired_reproducible_and_stratified():
    pairs = [
        {"instance_id": f"a{i}", "repo": "a/a", "baseline": {"resolved": 0, "seconds": 10.0 + i},
         "gated": {"resolved": 1, "seconds": 12.0 + i}}
        for i in range(3)
    ] + [
        {"instance_id": f"b{i}", "repo": "b/b", "baseline": {"resolved": 1, "seconds": 20.0 + i},
         "gated": {"resolved": 0, "seconds": 25.0 + i}}
        for i in range(2)
    ]
    left = analyze(pairs, set(), bootstrap_reps=100, seed=31)
    right = analyze(pairs, set(), bootstrap_reps=100, seed=31)
    assert left == right
    assert left["n_pairs"] == left["cost"]["eligible_pair_n"] == 5
    assert left["gated_only"] == 3
    assert left["baseline_only"] == 2
    assert left["discordant_odds_ratio"] == 1.5
    assert len(left["cost"]["p90_ratio_95_ci"]) == 2


def test_preregistration_public_manifest_omits_gold_fields(tmp_path):
    repos = [
        "astropy/astropy", "django/django", "matplotlib/matplotlib", "psf/requests",
        "pydata/xarray", "pylint-dev/pylint", "pytest-dev/pytest",
        "scikit-learn/scikit-learn", "sphinx-doc/sphinx", "sympy/sympy",
    ]
    rows = [{
        "instance_id": repo.replace("/", "__") + "-1", "repo": repo,
        "base_commit": "a" * 40, "difficulty": "<15 min fix",
        "image": "swebench/test:latest", "patch": "gold", "test_patch": "gold-test",
    } for repo in repos]
    dataset = tmp_path / "source.json"
    dataset.write_text(json.dumps(rows), encoding="utf-8")
    exclusion = Path(__file__).resolve().parents[1] / "benchmarks/swebench/manifests/exclude-historical-49.json"
    manifest_path = tmp_path / "public.json"
    host_path = tmp_path / "host.json"
    prepare(dataset, exclusion, manifest_path, host_path, count=10, seed=2026,
            quotas={repo: 1 for repo in repos})
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    host = json.loads(host_path.read_text(encoding="utf-8"))
    assert len(manifest["tasks"]) == len(host) == 10
    assert all(set(row) == {"instance_id", "repo", "base_commit", "difficulty", "image"}
               for row in manifest["tasks"])
    assert all(row["image"].startswith("logicstar/") for row in host)
    assert all("patch" not in row and "test_patch" not in row for row in manifest["tasks"])
