# macOS release

`./scripts/build_macos.sh` builds `dist/RAWPhotoCurator.app` with PyInstaller. Without
`APPLE_SIGNING_IDENTITY` it deliberately produces an unsigned development artifact. For a public
release, import an Apple Developer ID Application certificate in CI, set the identity, sign with
the hardened runtime, notarize with `notarytool`, staple the ticket, and verify with both
`codesign --verify --deep --strict` and `spctl --assess`.

The GitHub Actions packaging job validates an unsigned app because this private repository does
not contain signing credentials. Never commit certificates, passwords, or notarization tokens.
