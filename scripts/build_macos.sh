#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
RAW_CURATOR_PYINSTALLER_CACHE="$PWD/build/pyinstaller-cache"
export PYINSTALLER_CONFIG_DIR="$RAW_CURATOR_PYINSTALLER_CACHE"
.venv/bin/python scripts/make_macos_icon.py
.venv/bin/pyinstaller --noconfirm packaging/raw-photo-curator.spec
if [ -n "${APPLE_SIGNING_IDENTITY:-}" ]; then
  codesign --force --deep --options runtime --sign "$APPLE_SIGNING_IDENTITY" dist/RAWPhotoCurator.app
  codesign --verify --deep --strict dist/RAWPhotoCurator.app
else
  echo "APPLE_SIGNING_IDENTITY is unset; produced an unsigned local build."
fi
