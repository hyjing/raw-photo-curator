# Performance and benchmark

Measured on the local Alaska set (355 Sony ARW files):

- warm scan: 355/355 cache hits, about 0.14–0.16 seconds;
- cold 30-photo RAW linear-metric pass: about 23.27 seconds;
- preference context load from 1,065 cached analyzer records: about 52 ms;
- pairwise model training with 20 simulated labels: about 10 ms;
- prediction over all cached records: about 8 ms;
- serialized profile model: about 1 KB.
- optional NIMA pass over 355 cached JPEG previews: 4.69 seconds;
- optional YuNet + expression pass over the same previews: 8.07 seconds, with two real
  people-containing images detected and occluded eyes safely reported as unknown.

Real local feedback evaluation on 2026-08-29 used 13 Travel decisions (10 keep, 3 reject). A
deterministic stratified holdout retained 10 training photos and 3 test photos. The generic prior,
explicit Travel profile, and personal ranker each scored 100% on the two available positive/negative
test pairs (Top-5 hit rate 1.0, NDCG 1.0). Personalization therefore did not exceed either baseline;
the sample is far too small for a general accuracy claim. The result is reproducible with
`raw-curator evaluate-personal reports/alaska-cache-validation --profile travel`.

The first real Phase 2 correction covered five Alaska photos that the user merged into one group.
Against those 10 labeled pairs, a fresh automatic grouping produced 4 predicted pairs, all correct:
precision 1.0, recall 0.4, and F1 0.5714. This exposes conservative under-grouping and is
reproducible with `raw-curator evaluate-corrections reports/alaska-cache-validation`.

These are development-machine measurements, not universal guarantees. First-time optional plugin
backfill can take several minutes; subsequent runs reuse plugin-specific cache entries. Run the
same folder twice and inspect the scan summary to reproduce the warm-cache result.
