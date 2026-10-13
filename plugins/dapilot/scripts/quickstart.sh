#!/usr/bin/env bash
# /dapilot:quickstart: set this machine up, show the 3DAPI wordmark, open the Integrations page.
#
#   quickstart.sh [port]        default port 3106
#
# What it prints is what a person sees, so it says little: the wordmark, one line per step,
# the page's address, and the connection table. Details go to the setup script's own log.
#
# 1. scripts/dev/shop.sh setup, when this is a dapilot checkout: tools (brew or apt), the
#    Python venv and packages, the frontend packages, store/.env and config from their
#    templates. It asks nothing (no terminal), so sign-ins are left to the page. It stops at
#    "store/.env has no value for ..." on a fresh machine, which is expected: the page fills them.
# 2. integrate.py serve: the Integrations page, opened in the browser.
# 3. integrate.py status: what is connected.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="${DAPILOT_PROJECT_DIR:-${CLAUDE_PROJECT_DIR:-$PWD}}"
PORT="${1:-3106}"
STORE="${DAPILOT_STORE_DIR:-$ROOT/store}"
case "$STORE" in /*) ;; *) STORE="$ROOT/${STORE#./}";; esac
export DAPILOT_STORE_DIR="$STORE" DAPILOT_PROJECT_DIR="$ROOT"
PY=python3
command -v python3 > /dev/null || { echo "Python 3 is not installed. Install it, then run this again."; exit 2; }

"$PY" "$HERE/integrate.py" banner
echo

if [ -x "$ROOT/scripts/dev/shop.sh" ]; then
  echo "Setting up this computer: tools, Python packages, store files. The first run can take a few minutes."
  out=$(mktemp)
  t=$SECONDS
  "$ROOT/scripts/dev/shop.sh" setup < /dev/null > "$out" 2>&1; code=$?
  logfile=$(grep -oE 'runs/shop-[0-9T]+\.log' "$out" | head -1)
  case $code in
    0) echo "Environment ready ($((SECONDS - t)) s)." ;;
    2) echo "Environment ready ($((SECONDS - t)) s). Credentials are next: fill them in on the page." ;;
    *) echo "Environment set up with notes ($((SECONDS - t)) s). Details: ${logfile:+$ROOT/$logfile}" ;;
  esac
  rm -f "$out"
else
  mkdir -p "$STORE"
  echo "Not a dapilot checkout, so no environment setup here. The Integrations page still works."
fi
echo

res=$("$PY" "$HERE/integrate.py" serve --port "$PORT")
url=$(printf '%s' "$res" | "$PY" -c 'import json,sys
try: print(json.load(sys.stdin).get("url") or "")
except Exception: print("")')
if [ -n "$url" ]; then
  echo "Your Integrations page is open: $url"
  echo "Fill in each account there. Come back when the rows are green."
else
  reason=$(printf '%s' "$res" | "$PY" -c 'import json,sys
try: print(json.load(sys.stdin).get("reason") or "")
except Exception: print("")')
  echo "The page could not start${reason:+: $reason}. Try: quickstart.sh $((PORT + 1))"
fi
echo

"$PY" "$HERE/integrate.py" status
exit 0
