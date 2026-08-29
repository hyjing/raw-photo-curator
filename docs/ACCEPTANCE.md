# Roadmap acceptance

Roadmap checkboxes describe implemented capabilities. Release readiness is evaluated separately
against the selected local catalog and real user feedback:

```bash
raw-curator acceptance reports/alaska-cache-validation \
  --expected-photos 355 \
  --output reports/alaska-cache-validation/acceptance.json
```

For Phase 2 grouping precision and recall, add a manually labeled JSON file. Each inner array is
one ground-truth similarity group and contains the original absolute photo paths:

```json
{
  "groups": [
    ["/photos/DSC00001.ARW", "/photos/DSC00002.ARW"],
    ["/photos/DSC00110.ARW", "/photos/DSC00111.ARW", "/photos/DSC00112.ARW"]
  ]
}
```

```bash
raw-curator acceptance reports/alaska-cache-validation \
  --expected-photos 355 \
  --group-labels grouping-labels.json
```

The command does not synthesize decisions. It reports `missing` until the local database contains
real keep/reject feedback, a persisted group correction, a trained preference model, and a
non-empty manual grouping label set. This distinction prevents simulated fixtures from being
presented as photographer acceptance evidence.

Generate a reproducible Phase 5 holdout report directly from the report directory:

```bash
raw-curator evaluate-personal reports/alaska-cache-validation --profile travel
```

Current Alaska evidence (2026-08-29): 355 photos, 100% catalog cache coverage, zero failed jobs,
17 cached criteria, and 13 real Travel decisions. The stratified holdout used 10 training and 3
test photos. All three scoring modes reached 100% pairwise accuracy over only two test pairs, so the
tool truthfully reports that personalization has **not** exceeded the baseline. This is pipeline
evidence, not a statistically strong product claim. The grouping gate was subsequently completed
from one real five-photo merge correction. Rebuilding the automatic grouping inside that labeled
universe produced two groups: precision 1.0, recall 0.4, and F1 0.5714 over 10 labeled pairs. This
shows conservative under-grouping rather than false merges and is an initial acceptance result,
not a broad accuracy claim.
