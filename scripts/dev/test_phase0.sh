#!/bin/bash
# Autonomous test for Phase 0. Two layers:
#   level 0 (default)  no model: validator, ledger round trip, dapi.py evals, analyze script. Seconds.
#   --live             also runs the real director headlessly via `claude -p` and asserts on the ledger. Minutes, costs tokens.
# Exit 0 only if every assertion passes. Run from the repo root.
set -euo pipefail
cd "$(dirname "$0")/../.."
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
check "dapi ops lists 30 operations" "[ \$($PY $S/dapi.py --spec $SPEC ops | j \"len(d['operations'])\") = 30 ]"
check "dapi validate rejects bad body" "! $PY $S/dapi.py --spec $SPEC validate createQuote --body plugins/dapilot/skills/dapi-request/eval/bad-create-quote.json"
check "dapi gated op dry-runs without --confirm" "DAPI_API_URL=http://127.0.0.1:1 DAPI_API_KEY=k $PY $S/dapi.py --spec $SPEC call updatePrintFarmOrderStatus --path orderId=X --body plugins/dapilot/skills/dapi-request/eval/status.json | j \"d['dry_run']\" | grep -q True"
check "validator rejects malformed result" "! echo '{\"ok\":true}' | $PY $V"
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
  echo "== level 1: live director via claude -p (spawns lead and workers) =="
  RUN=live-$(date -u +%Y%m%dT%H%M%S); OUT=$(mktemp)
  claude -p --plugin-dir plugins/dapilot --dangerously-skip-permissions --output-format json \
    "/dapilot:run-intake fixtures $RUN" > "$OUT" 2>/dev/null || true
  check "claude -p returned without error" "[ \$(j \"d.get('is_error')\" < $OUT) = False ]"
  check "run state exists" "[ -f runs/$RUN/state.json ]"
  check "all enabled stages done" "[ \$($PY $L status --run $RUN | j \"d['counts']['failed']+d['counts']['blocked']+d['counts']['queued']+d['counts']['running']\") = 0 ]"
  check "report written" "[ -f runs/$RUN/report.md ]"
  check "every work dir has analysis.json" "for d in work/*/; do [ -f \$d/analysis.json ] || exit 1; done"
  echo "live run cost (claude -p total_cost_usd): $(j "d.get('total_cost_usd')" < "$OUT"), duration s: $(j "round(d.get('duration_ms',0)/1000)" < "$OUT")"
fi

echo; echo "passed $pass, failed $fail"; [ $fail -eq 0 ]
