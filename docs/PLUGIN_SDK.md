# Plugin SDK 1.0

Import the stable contract from `raw_photo_curator.sdk`; do not import server or catalog internals.
An analyzer declares a namespaced `id`, semantic `version`, manifest, criterion definitions,
availability, and an `analyze` method. See `src/raw_photo_curator/example_plugin.py` for a complete
zero-download example.

Publish the analyzer class as a Python entry point so the registry discovers it without core edits:

```toml
[project.entry-points."raw_photo_curator.analyzers"]
my_analyzer = "my_package.plugin:MyAnalyzer"
```

Rules:

- Never modify the source photo.
- Return confidence and structured evidence for every result.
- Report unavailable/degraded explicitly when a model or device is missing.
- Do not access the network unless the user separately authorizes it.
- Bump the plugin or criterion version whenever output semantics change.

Run `pytest tests/test_sdk.py` as the compatibility check. SDK 1.x preserves the public names
exported by `raw_photo_curator.sdk`; breaking changes require SDK 2.0.
