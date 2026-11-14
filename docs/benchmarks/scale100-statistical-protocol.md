# Scale-100 Statistical Protocol

This protocol was fixed with the sample before the first Agent run. The sample
is 100 unique Verified instances from 10 repositories, with baseline and gated
runs paired by instance. It is not pooled with the historical 49 distinct IDs.

## Primary outcome

On complete, consistent official grading pairs, count gated-only resolutions
`b` and baseline-only resolutions `c`. Report both arm totals, the paired risk
difference, and the two-sided exact McNemar test, calculated as the exact
binomial test of `b` successes among `b+c` discordant pairs with probability
0.5. If there are no discordant pairs, p=1. Report the discordant odds ratio
`b/c`; if `c=0`, the point estimate is infinite, and if there are no discordant
pairs it is not estimable. Its 95% exact conditional interval is the
Clopper-Pearson interval for the discordant-pair binomial probability mapped to
odds. Alpha is 0.05. The test is two-sided, and no expected direction or
significance is assumed.

For calibration, with 10 discordant pairs, counts 8:2 yield a two-sided exact
p-value about 0.109; 9:1 yield about 0.021. The planned 100 pairs do not
guarantee 10 discordances, statistical power, or significance.

## Cost

The primary cost measure is gated/baseline P90 wall-clock seconds among pairs
without externally evidenced host sleep or interruption spanning either mode.
Exclude both modes for such a pair from the primary cost comparison, retain raw
seconds, list excluded IDs, and show all-pairs wall-clock P90 as sensitivity.
Never infer or subtract sleep time from elapsed seconds.

Calculate P90 with linear interpolation at `(n-1)*0.90`. For the 95% percentile
interval, perform 10,000 paired bootstrap replicates, sampling within each
repository stratum and retaining baseline/gated values for each selected pair.
Use fixed seed 2026 and report each arm's P90, their ratio, interval, and valid
pair count. Pairs with missing or failed grading evidence have no assigned cost
and block the final complete-pair analysis until reviewed.

## Mechanism metrics and subgroups

Sum persisted gated `stats.json` values for `test_preservation_intercepts`.
Separately count blocked composite test commands in gated traces. Describe
results by repository, difficulty time bucket, gate completion, and historical
anomaly type without subgroup significance claims. Compare the direction with
earlier batches descriptively; do not pool them.

## Stop and completion rules

Stop and preserve the batch on `model_call_error` or `grade_infra_failure`.
Resume only after reviewing saved evidence; do not turn infrastructure failures
into unresolved results. Other anomalies are recorded and classified without
changing the fixed sample. Completion requires 100 unique IDs, 200 unique
instance/mode results, 100 complete pairs, consistent official reports and
patch evidence, and passing `completion_verification.json`.
