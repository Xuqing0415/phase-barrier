# Scale-100 Verified Pre-registration

Status: fixed before any Scale-100 Agent run. The sample contains 100 new
instance IDs; each instance is run once in baseline and once in gated mode.

## Population and exclusions

- Source: `SWE-bench/SWE-bench_Verified`, test split, 500 rows.
- Frozen local source SHA256: `a10f66810a84bdfb40479421cd2992682ad7cd96e69e7acd88322e87e5b8748a`.
- The historical exclusion is the union of Lite Scale-20 (20 IDs), cached
  Verified-15 (15 IDs), and preregistered Verified-20 (20 IDs): 49 distinct IDs.
- The three-way overlaps are Lite/cached = 4, cached/preregistered = 2,
  Lite/preregistered = 0; there is no triple overlap. 33 excluded IDs occur in
  this 500-row Verified source, leaving 467 eligible rows.
- Flask and Seaborn have no eligible rows after exclusion. The fixed sample
  therefore covers all 10 remaining repositories; it does not reuse a
  historical task to claim 12-repository coverage.

The machine-readable exclusion IDs and provenance are in
`exclude-historical-49.json`. The selected public rows are in this JSON file's
matching `scale100-verified-2026-11-14.json`. That manifest contains only
instance ID, repository, base commit, difficulty bucket, and runtime image.
The selected full rows, including grading data, remain host-side under
`.pytest_tmp/scale100_preparation/host_dataset.json`; they are never mounted
into Agent containers. The runner creates a separate allowlisted Agent dataset.

## Fixed allocation

Selection uses seed 2026, exact repository quotas, and the existing deterministic
within-repository difficulty rotation. This dataset uses estimated-time buckets,
not easy/medium/hard labels.

| Repository | Eligible | Quota |
|---|---:|---:|
| astropy/astropy | 18 | 6 |
| django/django | 222 | 25 |
| matplotlib/matplotlib | 30 | 10 |
| psf/requests | 6 | 4 |
| pydata/xarray | 21 | 3 |
| pylint-dev/pylint | 9 | 7 |
| pytest-dev/pytest | 18 | 6 |
| scikit-learn/scikit-learn | 29 | 12 |
| sphinx-doc/sphinx | 42 | 8 |
| sympy/sympy | 72 | 19 |
| **Total** | **467** | **100** |

Observed selected difficulty buckets: `<15 min fix` 38, `15 min - 1 hour` 38,
`1-4 hours` 21, `>4 hours` 3. The >4-hour bucket has only three eligible
instances in the full pool, so thirds are not attainable under this dataset's
labels and the fixed per-repository allocation.

## Run protocol

- 100 fixed instances, paired baseline/gated, 200 official grading jobs.
- 60 turns, concurrency 2, `test_preservation=strict` for gated runs.
- Agent container DNS is explicitly set to `223.5.5.5` and `1.1.1.1`.
- Requested model is `deepseek-v4-flash`; the service has reported the alias
  `deepseek-flash` in earlier runs. The provider does not expose a pinned model
  weight revision, so the exact model build is not claimed as frozen.
- No result-driven replacement, sample reduction, or significance-based early
  stop is permitted. Infrastructure/model-call errors halt the batch for
  investigation; they are not coded as unresolved outcomes.

## Primary outcome analysis

For complete official pairs, let `b` be gated-only resolutions and `c` be
baseline-only resolutions. Report both paired solve counts, the risk difference,
and the two-sided exact McNemar/binomial p-value `min(1, 2 * BinomCDF(min(b,c); b+c, 0.5))`.
When `b+c=0`, report p=1 and the discordant-pair odds ratio as not estimable.
Otherwise report `b/c` and a 95% exact conditional interval: form the two-sided
Clopper-Pearson interval for `b` successes in `b+c` discordant pairs, then map
each probability endpoint `p` to odds `p/(1-p)`. Use alpha 0.05. No direction or
significance is assumed in advance.

Gate completion is a separate process outcome, not a substitute for official
resolution. All 100 planned pairs must have two consistent official grading
records before final completion is claimed.

## Cost analysis

The primary cost estimand is the ratio of gated to baseline P90 wall-clock
seconds. Exclude a pair from the primary cost sample only when external host
event evidence documents sleep or an interruption spanning either run; remove
both modes of that pair from the primary comparison. Preserve all raw seconds
and report an all-pairs wall-clock sensitivity analysis. Do not subtract an
estimated sleep duration.

Use the linear-interpolated empirical quantile (position `(n-1)*0.90`) and
10,000 paired bootstrap replicates, resampling instance pairs with replacement
within each repository stratum and keeping each sampled baseline/gated pair
together. The 2.5th and 97.5th percentiles of the bootstrap P90 ratio are the
95% percentile interval. Record the fixed bootstrap seed (2026), valid pair
count, per-mode P90 values, and excluded pair IDs. A missing/failed job is not
assigned zero cost.

## Mechanism and exploratory summaries

- Sum `test_preservation_intercepts` from persisted gated `stats.json` files;
  this counts strict rejections for undeclared stage-2 test removals/renames.
- Separately count blocked composite test commands from gated `trace.jsonl`.
- Repository, difficulty-bucket, gate-completion, and historical-anomaly-type
  summaries are descriptive and exploratory; do not make subgroup significance
  claims.
- Compare the Scale-100 direction with earlier batches descriptively. Do not
  pool these different batches into a common inferential estimate.

## Completion and stopping

Stop for `model_call_error` or `grade_infra_failure`; preserve evidence and
require review before resume. Other anomalies are recorded and classified.
Final acceptance requires 100 unique preregistered IDs, exactly 200 unique
instance/mode results, 100 complete pairs, fresh trace/state/stats/patch and
official grading evidence, and a passing `completion_verification.json`.
