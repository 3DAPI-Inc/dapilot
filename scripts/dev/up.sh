#!/usr/bin/env bash
# One command to run dapilot locally: checks, then the job API and the console.
#
#   scripts/dev/up.sh            check prerequisites, start server (:3100) and frontend (:8081), tail both
#   scripts/dev/up.sh --check    only the checks, exit non-zero if something is missing
#   scripts/dev/up.sh --resume   also start one director over every unfinished work dir
#
# What "keeps working" rests on, in one place:
#   - the server derives every job's stage from files, so a restart loses nothing
#   - a director per upload runs the fixed stage table; runs/<id>/ keeps its ledger,
#     report and every worker's raw output
#   - anything left half-done is listed at /runs/unfinished and picked up by
#     POST /runs/resume, this script's --resume, or resume_on_start in store/config.json
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=$(pwd)
STORE="${DAPILOT_STORE_DIR:-$ROOT/store}"
PY="$ROOT/.venv/bin/python"
ok=1

say() { printf '  %-14s %s\n' "$1" "$2"; }
need() { if [ -n "$2" ]; then say "$1" "$2"; else say "$1" "MISSING: $3"; ok=0; fi; }

echo "dapilot at $ROOT"
[ -x "$PY" ] && need python "$($PY --version 2>&1)" || need python "" "run scripts/dev/shop.sh setup"
need claude "$(command -v claude || true)" "install the Claude Code CLI"
need node "$(command -v node || true)" "install node for the frontend"
if [ -f "$STORE/.env" ]; then
  # shellcheck disable=SC1090
  set -a; source "$STORE/.env"; set +a
  need store/.env "$STORE/.env"
else
  need store/.env "" "copy plugins/dapilot/templates/.env.example to store/.env and fill it in"
fi
for v in DAPI_API_URL DAPI_API_KEY ETSY_KEYSTRING ETSY_SHARED_SECRET; do
  need "$v" "$( [ -n "${!v:-}" ] && echo set || true)" "add it to store/.env"
done
need policy "$( [ -f "$STORE/reference/pricing-policy.json" ] && echo "$STORE/reference/pricing-policy.json" || true)" "write store/reference/pricing-policy.json"
need etsy-token "$( [ -f "$STORE/etsy_token.json" ] && echo "$STORE/etsy_token.json" || true)" "run $PY plugins/dapilot/scripts/etsy_auth.py --env store/.env login"
need config "$( [ -f "$STORE/config.json" ] && echo "$STORE/config.json" || true)" "copy plugins/dapilot/templates/config.example.json to store/config.json"
[ -d "$ROOT/frontend/node_modules" ] && need frontend "node_modules present" || need frontend "" "cd frontend && npm install"
export DAPILOT_STORE_DIR="$STORE" DAPILOT_PROJECT_DIR="$ROOT" DAPILOT_PYTHON="$PY"
if [ "$ok" = 1 ]; then
  unfinished=$($PY plugins/dapilot/scripts/ledger.py init --run _probe --resume 2>/dev/null | $PY -c 'import json,sys;print(len(json.load(sys.stdin).get("added",[])))' || echo "?")
  rm -rf "$ROOT/runs/_probe"
  say unfinished "$unfinished work dir(s) with a stage still to run"
fi
[ "$ok" = 1 ] || { echo "fix the MISSING lines above"; exit 1; }
[ "${1:-}" = "--check" ] && exit 0

mkdir -p "$ROOT/runs"
echo
echo "starting the job API on :3100 and the console on :8081"
"$PY" -m uvicorn server.app:app --port 3100 > "$ROOT/runs/server.log" 2>&1 &
SERVER=$!
( cd frontend && npm run dev -- --host >> "$ROOT/runs/frontend.log" 2>&1 ) &
FRONT=$!
trap 'echo; echo "stopping"; kill $SERVER $FRONT 2>/dev/null; wait $SERVER $FRONT 2>/dev/null; exit 0' INT TERM
for _ in $(seq 1 30); do curl -sf http://localhost:3100/health > /dev/null && break; sleep 0.5; done
curl -s http://localhost:3100/health; echo
if [ "${1:-}" = "--resume" ]; then
  echo "resuming unfinished work:"; curl -s -X POST http://localhost:3100/runs/resume; echo
fi
echo
echo "  console   http://localhost:8081"
echo "  job API   http://localhost:3100/docs"
echo "  logs      runs/server.log  runs/frontend.log  runs/<run>/director.log"
echo "  ctrl-c stops both; directors already running finish on their own"
echo
tail -n 0 -f "$ROOT/runs/server.log" "$ROOT/runs/frontend.log" &
wait $SERVER $FRONT
