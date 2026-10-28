#!/usr/bin/env bash
# Start the interactive demo. Open the printed URL in a browser.
set -e
cd "$(dirname "$0")"
python3 build_webui.py >/dev/null 2>&1 || true
exec python3 -m mirage_demo.server --models models.yaml --port "${1:-8000}"
