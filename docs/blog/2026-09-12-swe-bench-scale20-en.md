---
title: "We ran a stage-gate in front of an AI coding agent on SWE-bench Lite: 90% -> 100% (n=20), and the bugs were ours"
date: 2026-09-12
tags: [AI Agents, SWE-bench, software engineering, evaluation, Python]
---

# A stage gate in front of a coding agent: 90% -> 100% on SWE-bench Lite (n=20)

**TL;DR** — We wrapped a DeepSeek-based coding agent with [phase-barrier](https://github.com/Xuqing0415/phase-barrier),
a stage gate that forces `requirements -> spec -> tests -> implementation -> test run -> delivery`.
Inside the official SWE-bench eval containers, over 20 paired Lite instances: baseline 18/20 (90%),
gated 20/20 (100%), 78 gate interceptions, 0 empty patches, 0 regressions. Wall-clock cost: ~1.8x.
**n=20 is a same-condition comparison, not a leaderboard claim.** Raw CSV and one-command recompute are in the repo.

## Setup

- **Dataset**: `SWE-bench/SWE-bench_Lite`, `test` split, 20 instances (django 4, sympy 4, astropy 2,
  matplotlib 2, + seaborn / flask / requests / xarray / pylint / pytest / scikit-learn / sphinx).
- **Arms**: `baseline` (no gate) vs `gated` (`AntiShortcutSkill`), same instances, paired.
- **Model**: one model, one prompt, one temperature; 60 tool-call turns max per instance.
- **Runtime**: agents run *inside* the official `swebench/sweb.eval.x86_64.*` images;
  grading runs the official `swebench==5.0.2` harness (`/eval.sh`, FAIL_TO_PASS / PASS_TO_PASS) on the host.

## Results

| Arm | n | resolved | rate | Wilson 95% | interceptions | stage-6 + clean delivery | empty patches | mean wall-clock |
|---|---|---|---|---|---|---|---|---|
| baseline | 20 | 18 | 90% | [69.9%, 97.2%] | 0 | - | 0 | 262s |
| gated | 20 | 20 | 100% | [83.9%, 100.0%] | 78 | 14 | 0 | 462s |

Paired comparison: 2 instances that failed without the gate passed with it
(`matplotlib-22711`, `sympy-11400`); 0 went the other way. The Wilson intervals overlap, so
**this is a directional result, not statistical significance** — that is exactly why we are scaling up.

## The interesting part: our first gated run was 65%

The first gated run scored **13/20**. Six instances were *permanently stuck at stage 2*
(test validation) and could never advance. Both root causes were in the gate, not the agent:

1. **Language misdetection.** Detection was "first marker file wins". django and sphinx have a
   root `package.json` (frontend/docs toolchain), so a Python repo was classified as
   `javascript` and `.py` tests were validated with JS heuristics -> "not enough assert keywords".
   Fix: on multiple markers, disambiguate by **source-file counts** (skipping `node_modules`,
   `.venv`, `build`, ...) and only fall back to the old priority order if that is inconclusive.
2. **Gate let the agent freeload off the existing test suite.** `validate_tests` scanned the whole
   checkout (decades of legacy fixtures), so the agent could "pass" stage 2 without writing any new
   test. Fix: `stage2_test_scope: changed` (default) validates only test files added/modified vs
   `HEAD`; non-git or clean-tree setups fall back to the old behavior.

After the fixes: the six stuck instances went **resolved 0 -> 1**, and gated went **13/20 -> 20/20**.
Regression tests reproduce both with real django/sphinx layouts, including a "reuse legacy tests to
cheat" bypass case.

## Two harness traps that silently corrupt agent evals

1. **Stale patches scored as fresh results.** When the agent container crashed early, the previous
   turn's `model_patch.diff` was still in the mount and the driver graded *it*. At one point 14 of
   20 rows were stale. Fix: clear the per-label directory before each run and assert the patch mtime
   is newer than the run start.
2. **Concurrent writes to a shared dependency volume.** Under Docker Desktop volume mounts,
   `mkdir` locks are not mutually exclusive, and parallel `pip install --target` corrupted the
   shared gate-deps volume (missing `typing_extensions`). Fix: bootstrap now does
   `rm -rf` -> install -> `import` verification -> only then writes `.ready`.

## What the gate costs and what it guarantees

- Cost: +6.7 turns and ~1.8x wall-clock on average.
- Guarantee: every delivery reached stage 6 with a test run that is green *and newer than the last
  source change* (`delivery_clean()`); nothing was delivered on unverified evidence.
- Note: 6 gated runs exhausted the 60-turn budget at stage 3/4 — the gate did *not* let them
  deliver, but the official grader only looks at the patch, so those patches still scored resolved.
  "Gate blocked the workflow" and "grader accepted the patch" are different claims.

## Reproduce

```bash
python benchmarks/swebench/analyze_results.py \
  --results benchmarks/swebench/results/scale20_lite_container.csv
# baseline resolved=18 (90%) wilson95=[69.9%,97.2%] intercepts=0
# gated    resolved=20 (100%) wilson95=[83.9%,100.0%] intercepts=78
# rescued=['matplotlib__matplotlib-22711', 'sympy__sympy-11400'] regressed=[]
```

Report: `docs/benchmarks/swe-bench-scale20.md`. Repo: <https://github.com/Xuqing0415/phase-barrier>.

## Suggested Hacker News / Dev.to posting

- **HN title**: `Show HN: A stage gate for coding agents - 90% to 100% on SWE-bench Lite (n=20)`
- **HN first comment**: post the "our first gated run was 65%" section verbatim plus the reproduce
  command; disclose that n=20 is directional and the two rescued instances by id.
- **Dev.to tags**: `ai`, `agents`, `swebench`, `python`, `opensource`.
