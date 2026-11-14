"""Verify complete Scale-100 evidence and compute preregistered statistics."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))
from scripts.verified20_checks import audit_persisted_run


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires nonempty data")
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (position - low) * (ordered[high] - ordered[low])


def binomial_cdf(k: int, n: int, p: float) -> float:
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.comb(n, i) * p**i * (1 - p)**(n - i) for i in range(k + 1))


def exact_mcnemar(gated_only: int, baseline_only: int) -> float:
    n = gated_only + baseline_only
    if n == 0:
        return 1.0
    return min(1.0, 2.0 * sum(math.comb(n, i) for i in range(min(gated_only, baseline_only) + 1)) / 2**n)


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    if n == 0:
        raise ValueError("interval requires discordant pairs")
    tail = alpha / 2
    if k == 0:
        lower = 0.0
    else:
        lo, hi = 0.0, k / n
        for _ in range(80):
            mid = (lo + hi) / 2
            if 1 - binomial_cdf(k - 1, n, mid) < tail:
                lo = mid
            else:
                hi = mid
        lower = (lo + hi) / 2
    if k == n:
        upper = 1.0
    else:
        lo, hi = k / n, 1.0
        for _ in range(80):
            mid = (lo + hi) / 2
            if binomial_cdf(k, n, mid) > tail:
                lo = mid
            else:
                hi = mid
        upper = (lo + hi) / 2
    return lower, upper


def odds(probability: float) -> float:
    return math.inf if probability >= 1 else (0.0 if probability <= 0 else probability / (1 - probability))


def _label(instance_id: str, mode: str) -> str:
    return instance_id.replace("/", "_").replace("__", "_") + f"_{mode}_scale100_20261114"


def load_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def verify(manifest_path: Path, root: Path) -> tuple[dict, list[dict], list[str]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tasks = manifest["tasks"]
    expected = {(task["instance_id"], mode) for task in tasks for mode in ("baseline", "gated")}
    errors: list[str] = []
    prelaunch_path = root / "prelaunch.json"
    if prelaunch_path.exists():
        prelaunch = json.loads(prelaunch_path.read_text(encoding="utf-8"))
        if prelaunch.get("manifest_sha256") != hashlib.sha256(manifest_path.read_bytes()).hexdigest():
            errors.append("preregistered_manifest_changed_since_launch")
        for relative, expected_hash in prelaunch.get("source_hashes", {}).items():
            path = ROOT / relative
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
                errors.append("source_changed_since_launch:" + relative)
    else:
        errors.append("missing_prelaunch_snapshot")
    results_path = root / "results.csv"
    if not results_path.exists():
        rows = []
        errors.append("missing_results_csv")
    else:
        rows = load_rows(results_path)
    by_key: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row.get("instance_id", ""), row.get("mode", ""))
        if key not in expected or key in by_key:
            errors.append("unexpected_or_duplicate_result:" + repr(key))
        else:
            by_key[key] = row
    if len(rows) != 200:
        errors.append(f"expected_200_rows_got_{len(rows)}")
    if len(by_key) != 200:
        errors.append(f"expected_200_unique_jobs_got_{len(by_key)}")

    preservation = 0
    composite_blocked = 0
    verified_rows = []
    for task in tasks:
        iid = task["instance_id"]
        pair = {}
        for mode in ("baseline", "gated"):
            row = by_key.get((iid, mode))
            if row is None:
                continue
            label = _label(iid, mode)
            run_dir = root / "agent_runs" / label
            patch = run_dir / "model_patch.diff"
            stats_path = run_dir / "stats.json"
            report_path = root / "eval_runs" / "reports" / (label + ".json")
            prediction_path = root / "eval_runs" / "predictions" / (label + ".json")
            if row.get("note") != "graded":
                errors.append(f"not_officially_graded:{iid}:{mode}:{row.get('note')}")
                continue
            try:
                stats = json.loads(stats_path.read_text(encoding="utf-8"))
                report_blob = json.loads(report_path.read_text(encoding="utf-8"))
                official = report_blob[iid]
                predictions = json.loads(prediction_path.read_text(encoding="utf-8"))
                patch_text = patch.read_text(encoding="utf-8")
            except (OSError, json.JSONDecodeError, KeyError) as exc:
                errors.append(f"missing_or_invalid_evidence:{iid}:{mode}:{type(exc).__name__}")
                continue
            if stats.get("instance_id") != iid or stats.get("mode") != mode:
                errors.append(f"stats_identity_mismatch:{iid}:{mode}")
            if (not official.get("patch_successfully_applied") or official.get("infra_failure")
                    or int(bool(official.get("resolved"))) != int(row["resolved"])):
                errors.append(f"official_result_mismatch:{iid}:{mode}")
            if (not predictions or predictions[0].get("instance_id") != iid
                    or predictions[0].get("model_patch") != patch_text):
                errors.append(f"prediction_patch_mismatch:{iid}:{mode}")
            if len(patch_text) != int(row["diff_chars"]):
                errors.append(f"patch_size_mismatch:{iid}:{mode}")
            for key in ("turns", "seconds", "diff_chars", "gate_intercepts", "gate_completed"):
                try:
                    if float(stats[key]) != float(row[key]):
                        errors.append(f"stats_mismatch:{iid}:{mode}:{key}")
                except (KeyError, TypeError, ValueError):
                    errors.append(f"invalid_stats:{iid}:{mode}:{key}")
            if mode == "gated":
                if stats.get("test_preservation") != "strict":
                    errors.append(f"not_strict:{iid}")
                preservation += int(stats.get("test_preservation_intercepts", 0))
                if not audit_persisted_run(run_dir)["passed"]:
                    errors.append(f"gated_trace_audit_failed:{iid}")
                try:
                    events = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text(
                        encoding="utf-8").splitlines() if line.strip()]
                    composite_blocked += sum(bool(
                        event.get("blocked") is True
                        and int(event.get("stage_before", 9)) < 3
                        and re.search(r"\b(?:pytest|unittest|tox|nox)\b", str(
                            (event.get("args") or {}).get("command", "")), re.I)
                        and re.search(r"&&|;|\|\|", str((event.get("args") or {}).get("command", "")))
                        ) for event in events
                    )
                except (OSError, json.JSONDecodeError):
                    errors.append(f"missing_gated_trace:{iid}")
            pair[mode] = {**row, "seconds": float(row["seconds"]), "resolved": int(row["resolved"])}
        if len(pair) == 2:
            verified_rows.append({"instance_id": iid, "repo": task["repo"],
                                  "difficulty": task.get("difficulty", "-"),
                                  "baseline": pair["baseline"], "gated": pair["gated"]})
    proof = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "passed": not errors and len(verified_rows) == 100,
        "planned_instances": len(tasks), "unique_instances_with_verified_pairs": len(verified_rows),
        "expected_jobs": len(expected), "unique_result_jobs": len(by_key), "result_rows": len(rows),
        "gate_modes": {"gated_test_preservation": "strict"},
        "test_preservation_intercepts": preservation,
        "composite_test_command_intercepts": composite_blocked,
        "errors": errors,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    }
    (root / "completion_verification.json").write_text(
        json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return proof, verified_rows, errors


def analyze(pairs: list[dict], cost_exclusions: set[str], *, seed: int = 2026,
            bootstrap_reps: int = 10000) -> dict:
    unknown_exclusions = cost_exclusions - {pair["instance_id"] for pair in pairs}
    if unknown_exclusions:
        raise ValueError("cost exclusions are not selected instances: " + ", ".join(sorted(unknown_exclusions)))
    n = len(pairs)
    baseline_resolved = sum(pair["baseline"]["resolved"] for pair in pairs)
    gated_resolved = sum(pair["gated"]["resolved"] for pair in pairs)
    gated_only = sum(pair["baseline"]["resolved"] == 0 and pair["gated"]["resolved"] == 1 for pair in pairs)
    baseline_only = sum(pair["baseline"]["resolved"] == 1 and pair["gated"]["resolved"] == 0 for pair in pairs)
    discordant = gated_only + baseline_only
    if discordant:
        lo, hi = clopper_pearson(gated_only, discordant)
        or_ci = [odds(lo), odds(hi)]
        or_point = gated_only / baseline_only if baseline_only else math.inf
    else:
        or_ci, or_point = None, None
    eligible = [pair for pair in pairs if pair["instance_id"] not in cost_exclusions]
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for pair in eligible:
        by_repo[pair["repo"]].append(pair)
    rng = random.Random(seed)
    ratios = []
    if eligible:
        for _ in range(bootstrap_reps):
            sampled = [rng.choice(bucket) for repo in sorted(by_repo)
                       for bucket in [by_repo[repo]] for _ in bucket]
            base_p90 = percentile([p["baseline"]["seconds"] for p in sampled], 0.9)
            gate_p90 = percentile([p["gated"]["seconds"] for p in sampled], 0.9)
            ratios.append(gate_p90 / base_p90 if base_p90 else math.inf)
    all_base = [pair["baseline"]["seconds"] for pair in pairs]
    all_gate = [pair["gated"]["seconds"] for pair in pairs]
    cost_base = [pair["baseline"]["seconds"] for pair in eligible]
    cost_gate = [pair["gated"]["seconds"] for pair in eligible]
    return {
        "n_pairs": n,
        "baseline_resolved": baseline_resolved,
        "gated_resolved": gated_resolved,
        "baseline_only": baseline_only,
        "gated_only": gated_only,
        "risk_difference_gated_minus_baseline": (gated_resolved - baseline_resolved) / n if n else None,
        "mcnemar_exact_two_sided_p": exact_mcnemar(gated_only, baseline_only),
        "discordant_odds_ratio": or_point,
        "discordant_odds_ratio_95_ci": or_ci,
        "cost": {
            "eligible_pair_n": len(eligible), "excluded_pair_ids": sorted(cost_exclusions),
            "baseline_p90_seconds": percentile(cost_base, 0.9) if cost_base else None,
            "gated_p90_seconds": percentile(cost_gate, 0.9) if cost_gate else None,
            "p90_ratio": percentile(cost_gate, 0.9) / percentile(cost_base, 0.9) if cost_base else None,
            "p90_ratio_95_ci": [percentile(ratios, 0.025), percentile(ratios, 0.975)] if ratios else None,
            "bootstrap_replicates": bootstrap_reps, "bootstrap_seed": seed,
            "all_pairs_wall_clock": {
                "n": n,
                "baseline_p90_seconds": percentile(all_base, 0.9) if all_base else None,
                "gated_p90_seconds": percentile(all_gate, 0.9) if all_gate else None,
            },
        },
    }


def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def render_report(pairs: list[dict], proof: dict, analysis: dict) -> str:
    lines = ["# Scale-100 SWE-bench Verified Results", "", "## Primary results", "",
             "| Planned pairs | baseline resolved | gated resolved | baseline-only | gated-only | Exact McNemar p | Discordant OR (95% CI) |",
             "|---:|---:|---:|---:|---:|---:|---:|"]
    ci = analysis["discordant_odds_ratio_95_ci"]
    or_text = "not estimable" if ci is None else f"{analysis['discordant_odds_ratio']:.3g} ({ci[0]:.3g}, {ci[1]:.3g})"
    lines.append(f"| {analysis['n_pairs']} | {analysis['baseline_resolved']} | {analysis['gated_resolved']} | {analysis['baseline_only']} | {analysis['gated_only']} | {analysis['mcnemar_exact_two_sided_p']:.6g} | {or_text} |")
    lines += ["", "## Cost", "", "| Cost pairs | Baseline P90 (s) | Gated P90 (s) | Ratio | Bootstrap 95% CI |",
              "|---:|---:|---:|---:|---:|"]
    cost = analysis["cost"]
    ci_text = "not estimable" if cost["p90_ratio_95_ci"] is None else f"[{cost['p90_ratio_95_ci'][0]:.3f}, {cost['p90_ratio_95_ci'][1]:.3f}]"
    lines.append(f"| {cost['eligible_pair_n']} | {cost['baseline_p90_seconds']:.1f} | {cost['gated_p90_seconds']:.1f} | {cost['p90_ratio']:.3f} | {ci_text} |")
    lines += ["", f"All-pairs wall-clock P90: baseline {cost['all_pairs_wall_clock']['baseline_p90_seconds']:.1f}s, gated {cost['all_pairs_wall_clock']['gated_p90_seconds']:.1f}s (n={analysis['n_pairs']}).",
              f"Cost exclusions: {', '.join(cost['excluded_pair_ids']) or 'none with external interruption evidence'}.",
              "", "## Per-instance paired results", "", "| Instance | Repository | Difficulty | Baseline | Gated | Gate complete |",
              "|---|---|---|---:|---:|---:|"]
    for pair in pairs:
        lines.append(f"| {pair['instance_id']} | {pair['repo']} | {pair['difficulty']} | {pair['baseline']['resolved']} | {pair['gated']['resolved']} | {pair['gated']['gate_completed']} |")
    lines += ["", "## Mechanism metrics", "",
              f"- Strict test-preservation rejections: {proof['test_preservation_intercepts']}.",
              f"- Blocked composite test commands: {proof['composite_test_command_intercepts']}.",
              "- Gate completion is reported separately from official resolution.",
              "", "## Limitations", "",
              "- McNemar is two-sided at alpha 0.05; neither a direction nor significance was guaranteed.",
              "- Cost P90 and its percentile bootstrap interval are descriptive and sensitive to the sample and interruption exclusions.",
              "- Repository/difficulty subgroup summaries are exploratory; prior batches are not pooled.",
              "- The requested model name is not a pinned provider weight revision.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--cost-exclusions", type=Path)
    parser.add_argument("--bootstrap-reps", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    proof, pairs, errors = verify(args.manifest, args.run_root)
    if not proof["passed"]:
        print(json.dumps({"passed": False, "verified_pairs": len(pairs), "errors": errors[:20]}, ensure_ascii=False))
        return 1
    exclusions = set()
    exclusion_evidence = []
    if args.cost_exclusions and args.cost_exclusions.exists():
        raw = json.loads(args.cost_exclusions.read_text(encoding="utf-8"))
        for entry in raw.get("pairs", []):
            if not entry.get("reason") or not entry.get("evidence"):
                parser.error("each cost exclusion requires reason and external evidence")
            exclusions.add(entry["instance_id"])
            exclusion_evidence.append({key: entry[key] for key in ("instance_id", "reason", "evidence")})
    summary = analyze(pairs, exclusions, seed=args.seed, bootstrap_reps=args.bootstrap_reps)
    summary["cost"]["exclusion_evidence"] = exclusion_evidence
    summary["completion_verification_sha256"] = hashlib.sha256(
        (args.run_root / "completion_verification.json").read_bytes()).hexdigest()
    (args.run_root / "analysis.json").write_text(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (args.run_root / "scale100-results.md").write_text(render_report(pairs, proof, summary), encoding="utf-8")
    print(json.dumps(json_safe({"passed": True, "verified_pairs": len(pairs), "analysis": summary}), ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
