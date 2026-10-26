#!/bin/bash
# Autonomous test for Phase 0. Two layers:
#   level 0 (default)  no model: validator, ledger round trip, dapi.py evals, analyze script. Seconds.
#   --live             also runs run_intake.py on two fresh meshes (workers are real claude -p sessions) and asserts on the ledger. About a minute, about 0.20 USD.
#   --live-agents      the expensive variant: the director skill via claude -p, with model agents as director and lead. Minutes, about 0.50 USD.
# Exit 0 only if every assertion passes. Run from the repo root.
set -euo pipefail
cd "$(dirname "$0")/../.."
# store/.env carries ANTHROPIC_API_KEY (bills claude -p to the API), DAPI_* and DAPILOT_PYTHON.
if [ -f store/.env ]; then set -a; . store/.env; set +a; fi
PY=${DAPILOT_PYTHON:-$PWD/.venv/bin/python}
S=plugins/dapilot/scripts; L=$S/ledger.py; V=$S/validate_result.py; SPEC=plugins/dapilot/skills/dapi-request/eval/openapi.json
export DAPILOT_PROJECT_DIR=$PWD DAPILOT_PYTHON=$PY
pass=0; fail=0
check() { if eval "$2" >/dev/null 2>&1; then echo "ok    $1"; pass=$((pass+1)); else echo "FAIL  $1"; fail=$((fail+1)); fi; }
j() { $PY -c "import json,sys;d=json.load(sys.stdin);print($1)"; }

echo "== level 0: no model =="
check "python deps present" "$PY -c 'import trimesh, scipy, networkx, jsonschema'"
check "plugin manifest valid" "claude plugin validate plugins/dapilot"
check "analyze single: 1 body" "[ \$($PY $S/analyze_mesh.py fixtures/single.stl | j \"d['bodies']\") = 1 ]"
check "analyze multi: 8 bodies" "[ \$($PY $S/analyze_mesh.py fixtures/multi.stl | j \"d['bodies']\") = 8 ]"
check "contract: every schema example validates" "$PY $S/contract.py selftest >/dev/null"
# Undefined names, which py_compile does not see (a renamed function with callers left behind crashed every
# approve on staging, 2026-10-26). Runs when pyflakes is importable (pip install pyflakes); the runner's
# doctor.sh runs the same check at container start with the package in the image.
if $PY -c "import pyflakes" 2>/dev/null; then
  check "pyflakes: no undefined names in the scripts and the runner" \
    "[ -z \"\$($PY -m pyflakes $S/*.py runner/consumer.py runner/supervisor.py runner/triage.py 2>&1 | grep -v 'imported but unused\\|f-string is missing placeholders\\|redefinition of unused\\|assigned to but never used\\|unable to detect undefined names')\" ]"
else
  echo "skip  pyflakes not installed in the venv; the runner's doctor.sh runs this check"
fi
check "3DAPI ops lists 35 operations" "[ \$($PY $S/dapi.py --spec $SPEC ops | j \"len(d['operations'])\") = 35 ]"
check "3DAPI validate rejects bad body" "! $PY $S/dapi.py --spec $SPEC validate createQuote --body plugins/dapilot/skills/dapi-request/eval/bad-create-quote.json"
check "3DAPI gated op dry-runs without --confirm" "DAPI_API_URL=http://127.0.0.1:1 DAPI_API_KEY=k $PY $S/dapi.py --spec $SPEC call updatePrintFarmOrderStatus --path orderId=X --body plugins/dapilot/skills/dapi-request/eval/status.json | j \"d['dry_run']\" | grep -q True"
check "validator rejects malformed result" "! echo '{\"ok\":true}' | $PY $V"
if $PY -c "import sys; sys.path.insert(0, '$S'); import capture_turntable as c; sys.exit(0 if c.chrome() else 1)" 2>/dev/null; then
  RW=$(mktemp -d); cp fixtures/multi.stl "$RW/source.stl"
  check "preview draft produces 2 frames" "$PY $S/preview_model.py --workdir $RW --frames 2 --size 160 && [ -f $RW/renders/draft_01.png ]"
  rm -rf "$RW"
else echo "skip  preview draft (no Chrome)"; fi
GOOD='{"ok":true,"cached":true,"run":"r","item":"i","stage":"analyze","agent":"a","department":"catalogue","outputs":[],"needs_human":null,"reason":null,"summary":"s"}'
check "validator accepts well-formed result" "echo '$GOOD' | $PY $V"

RUN=t0-$$; TMPW=$(mktemp -d)
# Fresh meshes with unique hashes: the fixtures' work dirs may already be done,
# and a done item correctly yields no brief. Work dirs made here are removed at the end.
$PY - "$TMPW" "$$" <<'PYEOF'
import sys, trimesh
out, seed = sys.argv[1], int(sys.argv[2])
trimesh.creation.box(extents=(10 + seed % 7, 5, 3)).export(f"{out}/a.stl")
trimesh.creation.icosphere(subdivisions=1, radius=4 + (seed % 5)).export(f"{out}/b.stl")
PYEOF
NEW_ITEMS=$(cd "$TMPW" && shasum -a 256 a.stl b.stl | cut -c1-64)
check "ledger init finds 2 items" "[ \$($PY $L init --run $RUN --folder $TMPW | j \"d['items']\") = 2 ]"
check "ledger next returns 2 briefs" "[ \$($PY $L next --run $RUN | j \"len(d['briefs'])\") = 2 ]"
check "ledger next while running returns 0" "[ \$($PY $L next --run $RUN | j \"len(d['briefs'])\") = 0 ]"
ITEM=$($PY -c "import json;print(list(json.load(open('runs/$RUN/state.json'))['items'])[0])")
BAD_OK="{\"ok\":true,\"cached\":false,\"run\":\"$RUN\",\"item\":\"$ITEM\",\"stage\":\"analyze\",\"agent\":\"a\",\"department\":\"catalogue\",\"outputs\":[\"analysis.json\"],\"needs_human\":null,\"reason\":null,\"summary\":\"claims done\"}"
check "ledger requeues ok-without-output" "[ \$(echo '$BAD_OK' | $PY $L record --run $RUN --result - | j \"d['status']\") = queued ]"
check "ledger rejects malformed result" "! echo '{\"ok\":true}' | $PY $L record --run $RUN --result -"
check "ledger status reports work left" "$PY $L status --run $RUN | j \"d['work_left']\" | grep -q True"
check "ledger report writes report.md" "$PY $L report --run $RUN && [ -f runs/$RUN/report.md ]"
rm -rf "runs/$RUN" "$TMPW"; for h in $NEW_ITEMS; do rm -rf "work/$h"; done

if [ "${1:-}" = "--live" ]; then
  echo "== level 1: run_intake.py with real workers =="
  if [ -n "${ANTHROPIC_API_KEY:-}" ]; then echo "billing: ANTHROPIC_API_KEY set (API)"; else echo "billing: no ANTHROPIC_API_KEY, workers will use the logged-in subscription"; fi
  RUN=live-$(date -u +%Y%m%dT%H%M%S); TMPW=$(mktemp -d); OUT=runs/$RUN.summary.json; mkdir -p runs
  $PY - "$TMPW" "$$" <<'PYEOF'
import sys, trimesh
out, seed = sys.argv[1], int(sys.argv[2])
trimesh.creation.box(extents=(10 + seed % 7, 5, 3)).export(f"{out}/a.stl")
trimesh.creation.icosphere(subdivisions=1, radius=4 + (seed % 5)).export(f"{out}/b.stl")
PYEOF
  NEW_ITEMS=$(cd "$TMPW" && shasum -a 256 a.stl b.stl | cut -c1-64)
  echo "run $RUN: launching run_intake.py on 2 fresh meshes (about a minute)"
  $PY $S/run_intake.py "$TMPW" --run $RUN > "$OUT" 2> runs/$RUN.log || echo "run_intake.py exit code $?"
  check "run_intake.py reports ok" "[ \$(j \"d.get('ok')\" < $OUT) = True ]"
  check "run state exists" "[ -f runs/$RUN/state.json ]"
  check "all enabled stages done" "[ \$($PY $L status --run $RUN | j \"d['counts']['failed']+d['counts']['blocked']+d['counts']['queued']+d['counts']['running']\") = 0 ]"
  check "report written" "[ -f runs/$RUN/report.md ]"
  check "every item in this run has analysis.json" "$PY -c \"import json,sys;s=json.load(open('runs/$RUN/state.json'));sys.exit(0 if all(__import__('os').path.exists(i['workdir']+'/analysis.json') for i in s['items'].values()) else 1)\""
  check "cost recorded in ledger" "[ \$($PY $L status --run $RUN | j \"d['cost']['usd'] > 0\") = True ]"
  echo "live run: $(j "'%s USD, %ss, %s workers' % (d.get('usd_workers'), d.get('seconds'), d.get('workers_dispatched'))" < "$OUT")"
  rm -rf "$TMPW"; for h in $NEW_ITEMS; do rm -rf "work/$h"; done
fi

if [ "${1:-}" = "--live-agents" ]; then
  echo "== level 1b: live director via claude -p (model agents as director and lead) =="
  if [ -n "${ANTHROPIC_API_KEY:-}" ]; then echo "billing: ANTHROPIC_API_KEY set (API)"; else echo "billing: no ANTHROPIC_API_KEY, claude -p will use the logged-in subscription"; fi
  RUN=live-$(date -u +%Y%m%dT%H%M%S); mkdir -p runs; OUT=runs/$RUN.claude.json; ERR=runs/$RUN.claude.err
  # A 529 (API overloaded) or 5xx before the first turn is not a plugin failure. Retry the
  # launch up to 3 times with a pause; the ledger makes a resumed run idempotent anyway.
  echo "run $RUN: launching the director. This takes minutes and prints nothing until it ends."
  echo "  watch from another terminal:  $PY $L status --run $RUN"
  echo "  raw output lands in:          $OUT"
  for attempt in 1 2 3; do
    claude -p --plugin-dir plugins/dapilot --dangerously-skip-permissions --output-format json \
      "/dapilot:run-intake fixtures $RUN" > "$OUT" 2> "$ERR" || echo "claude -p exit code $? (attempt $attempt)"
    STATUS=$($PY -c "import json;d=json.load(open('$OUT'));print(d.get('api_error_status') or '')" 2>/dev/null || echo parse)
    if [ -z "$STATUS" ]; then break; fi
    echo "attempt $attempt: API error $STATUS before any work (zero tokens billed); waiting 90s and relaunching"
    sleep 90
  done
  check "claude -p returned without error" "[ \$(j \"d.get('is_error')\" < $OUT) = False ]"
  if ! $PY -c "import json,sys;d=json.load(open('$OUT'));sys.exit(0 if d.get('is_error') is False else 1)" 2>/dev/null; then
    echo "--- claude -p said (first 40 lines of $OUT and $ERR) ---"; head -c 3000 "$OUT"; echo; head -40 "$ERR"; echo "--- end ---"
  fi
  check "run state exists" "[ -f runs/$RUN/state.json ]"
  check "all enabled stages done" "[ \$($PY $L status --run $RUN | j \"d['counts']['failed']+d['counts']['blocked']+d['counts']['queued']+d['counts']['running']\") = 0 ]"
  check "report written" "[ -f runs/$RUN/report.md ]"
  check "every item in this run has analysis.json" "$PY -c \"import json,sys;s=json.load(open('runs/$RUN/state.json'));sys.exit(0 if all(__import__('os').path.exists(i['workdir']+'/analysis.json') for i in s['items'].values()) else 1)\""
  echo "live run cost (claude -p total_cost_usd): $(j "d.get('total_cost_usd')" < "$OUT"), duration s: $(j "round(d.get('duration_ms',0)/1000)" < "$OUT")"
fi

echo; echo "passed $pass, failed $fail"; [ $fail -eq 0 ]
