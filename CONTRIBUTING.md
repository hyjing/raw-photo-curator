# Contributing

Thanks for helping improve RAW Photo Curator. Small, focused pull requests are easiest to review.

## Before opening a pull request

1. Open an issue for behavior changes or new analyzers so the evidence, privacy boundary, and runtime
   cost can be agreed first.
2. Run `./scripts/bootstrap.sh`, then `ruff check .` and `pytest -q`.
3. Add tests for changed behavior and keep RAW processing non-destructive.
4. Never commit personal photos, feedback databases, downloaded models, credentials, or absolute
   local paths.

Analyzer contributions should follow [the plugin SDK](docs/PLUGIN_SDK.md) and report confidence,
evidence, version, cost, and unknown states instead of silently treating missing evidence as zero.

## Useful contribution areas

- RAW formats and camera-specific metadata;
- objective criteria with reproducible evaluation;
- burst and near-duplicate grouping;
- accessibility and localization;
- Windows and Linux packaging;
- signed and notarized macOS releases;
- documentation and real-world, consented evaluation sets.
