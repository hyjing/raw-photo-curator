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

These are development-machine measurements, not universal guarantees. First-time optional plugin
backfill can take several minutes; subsequent runs reuse plugin-specific cache entries. Run the
same folder twice and inspect the scan summary to reproduce the warm-cache result.
