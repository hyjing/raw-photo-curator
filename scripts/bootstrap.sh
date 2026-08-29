#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
echo "Ready. Run: .venv/bin/raw-curator serve /path/to/photos --output reports/live"
