#!/bin/sh
# Proves the image can run the plugin: every tool the pipeline needs answers, the plugin is at the
# expected release and read-only, the data directory is writable, and a real render goes through
# the bundled studio page in headless Chromium. Prints one line per check. Exit 1 on the first
# failure. With --serve (the image's default command), goes on to run the queue consumer once every
# check has passed, so a broken image stops loudly at start instead of taking a seller's message.
# With --hold, stays up idle after the checks, to prove the image somewhere with no queue.
set -u
fail() { echo "FAIL  $1"; exit 1; }
ok()   { echo "ok    $1"; }

echo "dapilot runner doctor"
[ -f /opt/dapilot/.release ] && ok "plugin $(cat /opt/dapilot/.release) at $DAPILOT_PLUGIN_DIR" || fail "plugin release marker missing"
[ -f "$DAPILOT_PLUGIN_DIR/.claude-plugin/plugin.json" ] || fail "plugin manifest missing"
v=$(claude --version 2>/dev/null) && ok "claude $v" || fail "claude CLI"
"$DAPILOT_PYTHON" -c 'import trimesh, scipy, networkx, numpy, jsonschema, fal_client' 2>/dev/null \
  && ok "python $("$DAPILOT_PYTHON" --version 2>&1 | cut -d' ' -f2) with the plugin's packages" || fail "python packages"
"$DAPILOT_PYTHON" "$DAPILOT_PLUGIN_DIR/scripts/contract.py" selftest >/dev/null \
  && ok "contract $(cat "$DAPILOT_PLUGIN_DIR/schema/VERSION"): every schema example validates" || fail "contract self-test (plugins/dapilot/schema)"
# Undefined names, which py_compile lets through: the consumer's approve handler called a function that
# had been renamed and crashed every approve on staging for a day (2026-10-26). Style warnings are not
# the question here; only a name that does not exist is.
names=$("$DAPILOT_PYTHON" -m pyflakes ./consumer.py ./supervisor.py ./triage.py "$DAPILOT_PLUGIN_DIR"/scripts/*.py 2>&1 \
  | grep -v "imported but unused\|f-string is missing placeholders\|redefinition of unused\|assigned to but never used\|unable to detect undefined names" || true)
[ -z "$names" ] && ok "pyflakes: no undefined names in the runner or the plugin's scripts" || fail "pyflakes: $names"
f=$(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f1-3) && ok "$f" || fail "ffmpeg"
"$CHROME" --headless=new --dump-dom about:blank >/dev/null 2>&1 && ok "chromium headless via $CHROME" || fail "chromium"
[ -f "$DAPILOT_PLUGIN_DIR/studio/index.html" ] && ok "studio page bundled" || fail "studio page missing: run scripts/dev/build_studio.sh and rebuild"
# the supervisor is a model (2026-10-26): its agent and skill must ship with the plugin, or the rules fallback would decide every stop
[ -f "$DAPILOT_PLUGIN_DIR/agents/supervisor.md" ] && [ -f "$DAPILOT_PLUGIN_DIR/skills/supervise-run/SKILL.md" ] && ok "supervisor agent and skill present" || fail "supervisor agent or skill missing from the plugin"
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

case "${1:-}" in
  --serve)
    # The consumer's settings, checked by name before it starts, so a missing one is a line in
    # this log rather than a traceback from the AWS SDK.
    missing=""
    for v in AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY DAPILOT_QUEUE_URL DAPILOT_BUCKET DAPILOT_BACKEND_URL DAPILOT_BACKEND_TOKEN; do
      eval "val=\${$v:-}"; [ -n "$val" ] || missing="$missing $v"
    done
    [ -n "${AWS_REGION:-}${AWS_DEFAULT_REGION:-}" ] || missing="$missing AWS_REGION"
    [ -z "$missing" ] || fail "environment not set:$missing"
    ok "consumer settings present"
    # The platform's credentials (the Etsy app key, fal, this container's Anthropic key, the public
    # API's address), fetched from the backend and tested against each platform before a message is
    # taken. They are ours, not a seller's: a failure here stops the container, which Coolify shows as
    # a failed deploy with the FAIL line in the log, instead of refusing every seller's run (2026-10-25).
    "$DAPILOT_PYTHON" ./consumer.py preflight || fail "platform credentials: see the lines above; fix them on the backend and redeploy"
    if [ -n "${DAPILOT_SUPERVISE_QUEUE_URL:-}" ]; then
      # The consumer runs the supervisor's loop in a second thread when this is set; its judgement
      # must hold before a single decision is made.
      "$DAPILOT_PYTHON" ./triage.py selftest >/dev/null && ok "triage self-test passed; supervising in this container" || fail "triage self-test failed"
    else
      echo "warn  DAPILOT_SUPERVISE_QUEUE_URL not set; stopped stages are not supervised"
    fi
    echo "starting the queue consumer"
    exec "$DAPILOT_PYTHON" ./consumer.py serve ;;
  --supervise)
    # The supervisor alone, as its own process: for splitting it from the runner later, or a laptop.
    # Normally not needed: --serve runs it inside the consumer when DAPILOT_SUPERVISE_QUEUE_URL is set.
    missing=""
    for v in AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY DAPILOT_SUPERVISE_QUEUE_URL DAPILOT_BACKEND_URL DAPILOT_BACKEND_TOKEN; do
      eval "val=\${$v:-}"; [ -n "$val" ] || missing="$missing $v"
    done
    [ -n "${AWS_REGION:-}${AWS_DEFAULT_REGION:-}" ] || missing="$missing AWS_REGION"
    [ -z "$missing" ] || fail "environment not set:$missing"
    "$DAPILOT_PYTHON" ./triage.py selftest >/dev/null && ok "triage self-test passed" || fail "triage self-test failed"
    echo "starting the supervisor"
    exec "$DAPILOT_PYTHON" ./supervisor.py serve ;;
  --hold)
    echo "holding"
    exec sleep infinity ;;
esac
