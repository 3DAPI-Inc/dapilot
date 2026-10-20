#!/bin/sh
# Proves the image can run the plugin: every tool the pipeline needs answers, the plugin is at the
# expected release and read-only, the data directory is writable, and a real render goes through
# the bundled studio page in headless Chromium. Prints one line per check. Exit 1 on the first
# failure. With --hold, stays up after the checks so a service with this as its command shows as
# running before the queue consumer exists.
set -u
fail() { echo "FAIL  $1"; exit 1; }
ok()   { echo "ok    $1"; }

echo "dapilot runner doctor"
[ -f /opt/dapilot/.release ] && ok "plugin $(cat /opt/dapilot/.release) at $DAPILOT_PLUGIN_DIR" || fail "plugin release marker missing"
[ -f "$DAPILOT_PLUGIN_DIR/.claude-plugin/plugin.json" ] || fail "plugin manifest missing"
v=$(claude --version 2>/dev/null) && ok "claude $v" || fail "claude CLI"
"$DAPILOT_PYTHON" -c 'import trimesh, scipy, networkx, numpy, jsonschema, fal_client' 2>/dev/null \
  && ok "python $("$DAPILOT_PYTHON" --version 2>&1 | cut -d' ' -f2) with the plugin's packages" || fail "python packages"
f=$(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f1-3) && ok "$f" || fail "ffmpeg"
"$CHROME" --headless=new --dump-dom about:blank >/dev/null 2>&1 && ok "chromium headless via $CHROME" || fail "chromium"
[ -f "$DAPILOT_PLUGIN_DIR/studio/index.html" ] && ok "studio page bundled" || fail "studio page missing: run scripts/dev/build_studio.sh and rebuild"
touch "$DAPILOT_DATA_DIR/.write-test" 2>/dev/null && rm -f "$DAPILOT_DATA_DIR/.write-test" && ok "$DAPILOT_DATA_DIR writable" || fail "$DAPILOT_DATA_DIR not writable (volume owner?)"
[ -w "$DAPILOT_PLUGIN_DIR/scripts" ] && fail "plugin is writable by the runtime user; it must be read-only" || ok "plugin read-only for $(id -un)"

# A real render: a cube through preview_model.py, which serves the bundled page and captures two
# frames in headless Chromium with software WebGL. This is the check that catches a Chromium flag,
# a font or a sandbox problem before a seller's run does.
d=$(mktemp -d) && cat > "$d/source.stl" <<'EOF'
solid cube
facet normal 0 0 -1
outer loop
vertex 0 0 0
vertex 10 10 0
vertex 10 0 0
endloop
endfacet
facet normal 0 0 -1
outer loop
vertex 0 0 0
vertex 0 10 0
vertex 10 10 0
endloop
endfacet
facet normal 0 0 1
outer loop
vertex 0 0 10
vertex 10 0 10
vertex 10 10 10
endloop
endfacet
facet normal 0 0 1
outer loop
vertex 0 0 10
vertex 10 10 10
vertex 0 10 10
endloop
endfacet
facet normal 0 -1 0
outer loop
vertex 0 0 0
vertex 10 0 0
vertex 10 0 10
endloop
endfacet
facet normal 0 -1 0
outer loop
vertex 0 0 0
vertex 10 0 10
vertex 0 0 10
endloop
endfacet
facet normal 0 1 0
outer loop
vertex 0 10 0
vertex 10 10 10
vertex 10 10 0
endloop
endfacet
facet normal 0 1 0
outer loop
vertex 0 10 0
vertex 0 10 10
vertex 10 10 10
endloop
endfacet
facet normal -1 0 0
outer loop
vertex 0 0 0
vertex 0 0 10
vertex 0 10 10
endloop
endfacet
facet normal -1 0 0
outer loop
vertex 0 0 0
vertex 0 10 10
vertex 0 10 0
endloop
endfacet
facet normal 1 0 0
outer loop
vertex 10 0 0
vertex 10 10 10
vertex 10 0 10
endloop
endfacet
facet normal 1 0 0
outer loop
vertex 10 0 0
vertex 10 10 0
vertex 10 10 10
endloop
endfacet
endsolid cube
EOF
out=$("$DAPILOT_PYTHON" "$DAPILOT_PLUGIN_DIR/scripts/preview_model.py" --workdir "$d" --frames 2 --size 128 2>&1 | tail -1)
[ -f "$d/renders/draft_01.png" ] && ok "WebGL render through the bundled studio page: $out" || fail "render failed: $out"
rm -rf "$d"

[ -n "${ANTHROPIC_API_KEY:-}" ] && ok "ANTHROPIC_API_KEY set (workers bill the API)" || echo "warn  ANTHROPIC_API_KEY not set; workers cannot run"
echo "all checks passed"

if [ "${1:-}" = "--hold" ]; then
  echo "no queue consumer in this image yet; holding"
  exec sleep infinity
fi
