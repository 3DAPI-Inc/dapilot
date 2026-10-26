#!/usr/bin/env bash
# The supervisor's fixtures, through the model (2026-10-26): every stop in
# plugins/dapilot/skills/supervise-run/eval/stops.jsonl is handed to runner/supervisor.py with a directory
# standing in for the backend, and the decision is compared with the fixture's `expect` (any of its
# entries passes: "retry", "leave", "redo:<stage>"). Costs a few cents a fixture and a minute in all, so
# it is not part of test_phase0.sh; run it after a change to the supervise-run skill or the agent.
#
#   scripts/dev/test_supervisor.sh [name-substring]
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY=${DAPILOT_PYTHON:-$ROOT/.venv/bin/python}
FIX="$ROOT/plugins/dapilot/skills/supervise-run/eval/stops.jsonl"
ONLY="${1:-}"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/dapilot-supervisor.XXXXXX")
export DAPILOT_PLUGIN_DIR="$ROOT/plugins/dapilot"
pass=0; fail=0
while IFS= read -r line; do
  [ -n "$line" ] || continue
  name=$("$PY" -c 'import json,sys; print(json.loads(sys.argv[1])["name"])' "$line")
  case "$name" in *"$ONLY"*) ;; *) continue ;; esac
  d="$WORK/$pass$fail"; mkdir -p "$d"
  "$PY" - "$line" "$d" <<'PYEOF'
import json, sys, pathlib
fx = json.loads(sys.argv[1]); d = pathlib.Path(sys.argv[2])
m = fx["message"]; item = fx.get("item") or {}
stop = {k: m.get(k) for k in ("item", "stage", "marker", "reason", "error", "previous", "retries", "attempt") if m.get(k) is not None}
outcome = {"items": [{"item": m["item"], "file": "model.3mf", **item}], "counts": {}, "gates_open": 0}
(d / "run.json").write_text(json.dumps({"run": m["run"], "seller": m["seller"], "status": "failed", "stops": [stop], "outcome": outcome}))
(d / "message.json").write_text(json.dumps(m))
PYEOF
  out=$("$PY" "$ROOT/runner/supervisor.py" once --message "$d/message.json" --local-backend "$d" 2>"$d/log.txt")
  got=$("$PY" - "$out" "$d" <<'PYEOF'
import json, sys, pathlib
r = json.loads(sys.argv[1]); d = pathlib.Path(sys.argv[2])
did = r.get("did")
if did == "redo": print(f"redo:{r.get('stage')}")
elif did in ("retried", "scheduled"): print("retry")
elif did == "left": print("leave")
else: print(did or "?")
PYEOF
)
  ok=$("$PY" -c 'import json,sys; fx=json.loads(sys.argv[1]); print("1" if sys.argv[2] in fx["expect"] else "0")' "$line" "$got")
  why=$("$PY" -c 'import json,sys; r=json.loads(sys.argv[1]); print((r.get("why") or "")[:140])' "$out")
  by=$(grep -o "the model could not decide[^\n]*" "$d/log.txt" | head -1)
  if [ "$ok" = "1" ]; then echo "ok    $name -> $got"; pass=$((pass+1)); else echo "FAIL  $name -> $got (wanted $("$PY" -c 'import json,sys; print(", ".join(json.loads(sys.argv[1])["expect"]))' "$line"))"; fail=$((fail+1)); fi
  [ -n "$why" ] && echo "      $why"
  [ -n "$by" ] && echo "      ($by)"
done < "$FIX"
echo "passed $pass, failed $fail  (details under $WORK)"
[ "$fail" -eq 0 ]
