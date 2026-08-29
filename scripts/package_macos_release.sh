#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

VERSION="${1:-$(git describe --tags --always --dirty 2>/dev/null || printf dev)}"
NAME="RAWPhotoCurator-${VERSION#v}-macOS"
APP="dist/RAWPhotoCurator.app"
DMG="dist/$NAME.dmg"
ZIP="dist/$NAME.zip"
STAGING="build/dmg-staging"

./scripts/build_macos.sh
rm -rf "$STAGING"
mkdir -p "$STAGING"
ditto "$APP" "$STAGING/RAWPhotoCurator.app"
ln -s /Applications "$STAGING/Applications"
hdiutil create -volname "RAW Photo Curator" -srcfolder "$STAGING" -ov -format UDZO "$DMG"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"

if [ -n "${APPLE_NOTARY_PROFILE:-}" ]; then
  xcrun notarytool submit "$DMG" --keychain-profile "$APPLE_NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
elif [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ] && [ -n "${APPLE_APP_PASSWORD:-}" ]; then
  xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_APP_PASSWORD" --wait
  xcrun stapler staple "$DMG"
else
  echo "Notarization credentials are unset; release artifacts are unsigned/unnotarized."
fi

(cd dist && shasum -a 256 "$NAME.dmg" "$NAME.zip" > SHA256SUMS)
echo "Release artifacts: $DMG, $ZIP, dist/SHA256SUMS"
