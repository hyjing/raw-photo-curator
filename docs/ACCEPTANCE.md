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

Current Alaska evidence (2026-08-29): 355 photos, 100% catalog cache coverage, zero failed jobs,
and 17 cached criteria. Human-feedback gates remain intentionally open until the photographer uses
the review and group-correction interfaces.
