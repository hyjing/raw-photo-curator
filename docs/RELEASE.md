# macOS release

## Local development build

```bash
.venv/bin/pip install -e '.[packaging,vision]'
./scripts/build_macos.sh
```

This creates `dist/RAWPhotoCurator.app`. Double-clicking the app opens a native Finder folder picker,
starts the loopback-only service, and opens the culling interface in the default browser.

## Distributable artifacts

```bash
./scripts/package_macos_release.sh v0.2.0
```

The script creates a drag-to-Applications DMG, a ZIP, and SHA-256 checksums in `dist/`. If signing
credentials are absent, the artifacts are intentionally unsigned and the script prints a warning.

## Signing and notarization

Import a **Developer ID Application** certificate, then set:

```bash
export APPLE_SIGNING_IDENTITY='Developer ID Application: Example (TEAMID)'
export APPLE_ID='release@example.com'
export APPLE_TEAM_ID='TEAMID'
export APPLE_APP_PASSWORD='app-specific-password'
./scripts/package_macos_release.sh v0.2.0
```

Alternatively, store notarization credentials in a keychain profile and set
`APPLE_NOTARY_PROFILE`. The package script submits the DMG and staples the ticket. Never commit a
certificate, password, API key, or keychain export.

Verify a signed release before publishing:

```bash
codesign --verify --deep --strict --verbose=2 dist/RAWPhotoCurator.app
spctl --assess --type execute --verbose=2 dist/RAWPhotoCurator.app
shasum -a 256 -c dist/SHA256SUMS
```

## GitHub release

`.github/workflows/release.yml` runs on `v*` tags or manually. Configure these repository secrets
for a public-quality release:

- `APPLE_SIGNING_IDENTITY`
- `APPLE_ID`
- `APPLE_TEAM_ID`
- `APPLE_APP_PASSWORD`

Create and push a release tag after tests pass:

```bash
git tag v0.2.0
git push origin v0.2.0
```

The workflow attaches the DMG, ZIP, and checksums to a generated GitHub Release. A manual workflow
run builds downloadable artifacts but does not create a Release entry.

## Release checklist

1. Run Ruff and the complete test suite.
2. Smoke-test Finder folder selection, cached analysis, rotation, keep/reject replacement, burst
   review, completion summary, copy export, and XMP export.
3. Verify the app bundle, DMG install path, signature, notarization, and checksums.
4. Confirm the README screenshots and version number.
5. Publish the tag, download the release on a clean Mac, and repeat the smoke test.
