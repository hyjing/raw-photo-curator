# Privacy

RAW Photo Curator binds only to `127.0.0.1`. Photos, thumbnails, EXIF, visual descriptors,
feedback, similarity groups, and preference models remain in the selected local report folder.
There is no telemetry or upload implementation. `anonymous_statistics` is stored as `off` by
default and cannot transmit anything in the current release.

Optional analyzers must declare download size, runtime cost, installation state, and privacy
boundary before they can be enabled. File exports and XMP sidecars are explicit local actions;
the original RAW bytes are never modified.
