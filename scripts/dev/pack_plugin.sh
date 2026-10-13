#!/usr/bin/env bash
# Build the zip that Customize > Plugins > Add > Upload plugin takes, after validating the plugin.
#   scripts/dev/pack_plugin.sh            -> out/dapilot-plugin-<version>.zip
#   scripts/dev/pack_plugin.sh --bump     -> raises the patch version in plugin.json first
# An uploaded plugin is a frozen copy: after every change, run this, remove the old copy in the
# app, upload the new zip, and start a new task. A GitHub marketplace install updates itself instead.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MANIFEST="$ROOT/plugins/dapilot/.claude-plugin/plugin.json"

if [ "${1:-}" = "--bump" ]; then
  python3 - "$MANIFEST" <<'EOF'
import json, sys
p = sys.argv[1]; m = json.load(open(p))
a, b, c = (int(x) for x in m["version"].split("."))
m["version"] = f"{a}.{b}.{c + 1}"
json.dump(m, open(p, "w"), indent=2); open(p, "a").write("\n")
print("version", m["version"])
EOF
fi

VERSION="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['version'])" "$MANIFEST")"
claude plugin validate "$ROOT/plugins/dapilot" >/dev/null || { echo "validation failed; run: claude plugin validate plugins/dapilot" >&2; exit 1; }

mkdir -p "$ROOT/out"
OUT="$ROOT/out/dapilot-plugin-$VERSION.zip"
rm -f "$OUT"
(cd "$ROOT/plugins" && zip -qr "$OUT" dapilot -x '*/__pycache__/*' '*.pyc' '*/.DS_Store')
echo "$OUT ($(du -h "$OUT" | cut -f1))"
echo "Customize > Plugins: remove the installed dapilot, then Add > Upload plugin with this file, then start a new task."
