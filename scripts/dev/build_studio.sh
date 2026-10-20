#!/usr/bin/env bash
# Build the console and copy it into the plugin as the studio page it ships.
#
#   scripts/dev/build_studio.sh            frontend/ -> plugins/dapilot/studio/
#
# capture_turntable.py serves plugins/dapilot/studio on a loopback port whenever no console is
# named, so an installed plugin renders the preview drafts and the product stills with headless
# Chrome and nothing else. The copy is committed: a marketplace install gets only the plugin
# directory. Run this after any change under frontend/src/lib/model or frontend/src/pages/Studio.tsx
# and commit the result with the change; release.sh warns when the copy is older than those files.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$ROOT/plugins/dapilot/studio"
cd "$ROOT/frontend"
[ -d node_modules ] || npm ci
npm run build >/dev/null
rm -rf "$OUT"
cp -R dist "$OUT"
echo "studio page built into plugins/dapilot/studio ($(du -sh "$OUT" | cut -f1))"
