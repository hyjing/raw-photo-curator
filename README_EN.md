# RAW Photo Curator

![RAW Photo Curator Alaska Top 5 demo](docs/assets/demo.gif)

RAW Photo Curator is a local-first, explainable photo culling tool for Sony ARW and common image
formats. It caches RAW previews and objective evidence, groups bursts and near-duplicates, learns a
small per-profile preference ranker from keep/reject feedback, and exports non-destructive XMP or
JSON/CSV selections. Photos and feedback stay on the machine.

## Quick start

```bash
./scripts/bootstrap.sh
.venv/bin/raw-curator serve /path/to/photos --output reports/live
```

Open `http://127.0.0.1:8765`, generate Top 5, then keep or reject. Kept photos stay in the current
five; rejected photos are immediately replaced. After five keeps, the next round excludes every
reviewed photo. The ranker uses explicit Travel/Portrait/Landscape/Wildlife/Custom rules first and
adds at most 40% learned preference when enough local evidence exists.

## What is technically interesting

- incremental, versioned SQLite feature cache for RAW workflows;
- time/EXIF/hash/embedding similarity groups with persisted corrections;
- criterion/plugin protocol with confidence, evidence, cost, and graceful degradation;
- 73-D strongly regularized pairwise learner, isolated per Profile;
- active selection balancing score, uncertainty, and visual diversity;
- reproducible local holdout metrics and learning curves;
- audited, reversible JSON/CSV/XMP/copy/link export workflow.

See [System Design](DESIGN.md), [Roadmap](ROADMAP.md), [Plugin SDK](docs/PLUGIN_SDK.md),
[Privacy](docs/PRIVACY.md), [Performance](docs/PERFORMANCE.md), and
[Models & limitations](docs/MODELS.md).

Optional local inference is explicit: install `.[vision]`, then run
`raw-curator install-model face` and/or `raw-curator install-model aesthetic`. Downloads are pinned
and checksum-verified. Face/eye/expression and NIMA results retain confidence and evidence; an
occluded or undetected eye stays unknown instead of being mislabeled as a blink.
