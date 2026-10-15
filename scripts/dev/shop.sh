#!/usr/bin/env bash
# The whole shop process, one step at a time, with the terminal saying where it is.
#
#   scripts/dev/shop.sh setup                     everything a fresh checkout needs: installs missing tools (brew, apt, dnf;
#                                                 sudo asks once), .venv, frontend packages, store files, Etsy/Instagram/Claude
#                                                 sign-ins, then check. Safe to rerun. SHOP_NO_INSTALL=1 only reports.
#   scripts/dev/shop.sh check [--offline]         is the setup ready: env, files, tools, live credentials, per stage
#   scripts/dev/shop.sh run <file|folder> [--no-approve] [--dry-run]
#                                                 setup -> check -> services -> intake up to the price -> price gate
#                                                 -> design, copy, publish -> where every item ended
#   scripts/dev/shop.sh status [hash ...]         where each item is, read from work/<hash>/
#   scripts/dev/shop.sh approve <hash> [price] [reason]   the price gate by hand; starts design onwards
#   scripts/dev/shop.sh resume                    run every work dir with a stage left
#
# Nothing here decides anything: run_intake.py runs the stages, server/app.py holds the
# price gate (and its break-even check), shop_inspect.py reads files. This script
# orders them, installs and starts what is missing, and logs every step, every child's
# output and how long each took to runs/shop-<time>.log. SHOP_TRACE=1 also logs each
# shell command it runs.
set -uo pipefail
cd "$(dirname "$0")/../.."
ROOT=$(pwd)
# store/.env, never over what the shell already set (the same rule as run_intake.py and server/app.py)
load_env() {
  local line k v
  while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    k=${BASH_REMATCH[2]}; v=${BASH_REMATCH[3]}
    v=${v%%[[:space:]]#*}; v="${v%"${v##*[![:space:]]}"}"
    case "$v" in \"*\") v=${v#\"}; v=${v%\"};; \'*\') v=${v#\'}; v=${v%\'};; esac
    [ -z "$v" ] || [ -n "${!k+x}" ] || export "$k=$v"
  done < "$1"
}
# the store: $DAPILOT_STORE_DIR, else this checkout's store/ when it is set up, else ~/.dapilot (plugins/dapilot/scripts/store_path.py)
STORE="${DAPILOT_STORE_DIR:-$(DAPILOT_PROJECT_DIR="$ROOT" python3 "$ROOT/plugins/dapilot/scripts/store_path.py")}"
[ -f "$STORE/.env" ] && load_env "$STORE/.env"
case "$STORE" in /*) ;; *) STORE="$ROOT/${STORE#./}";; esac
# the interpreter: $DAPILOT_PYTHON, else this checkout's .venv, else one venv per machine under ~/.dapilot
if [ -n "${DAPILOT_PYTHON:-}" ]; then PY="$DAPILOT_PYTHON"; elif [ -x "$ROOT/.venv/bin/python" ] || [ "$STORE" = "$ROOT/store" ]; then PY="$ROOT/.venv/bin/python"; else PY="$HOME/.dapilot/venv/bin/python"; fi
VENV="${PY%/bin/python}"
[ -t 1 ] && [ -z "${NO_COLOR:-}" ] && export FORCE_COLOR=1   # shop_inspect.py colours through the log pipe
case "$PY" in /*) ;; *) PY="$ROOT/${PY#./}";; esac
export DAPILOT_STORE_DIR="$STORE" DAPILOT_PROJECT_DIR="$ROOT" DAPILOT_PYTHON="$PY"
API=http://localhost:3100
STUDIO="${DAPILOT_STUDIO_URL:-http://localhost:8081}"
INSPECT="$ROOT/scripts/dev/shop_inspect.py"
SCRIPTS="$ROOT/plugins/dapilot/scripts"
ETSY_SCOPES="listings_r listings_w shops_r"   # publish drafts and activates listings
STAMP=$(date -u +%Y%m%dT%H%M%S)
OS=$(uname -s)   # Darwin or Linux
mkdir -p "$ROOT/runs"
LOG="$ROOT/runs/shop-$STAMP.log"
STEP="-"
STEP_T=$SECONDS
STARTED=()

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then B=$'\033[1m'; R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; D=$'\033[2m'; N=$'\033[0m'; else B= R= G= Y= D= N=; fi
strip() { sed -E 's/\x1b\[[0-9;]*m//g'; }
log()  { local line; line="$(date +%H:%M:%S) [$STEP] $*"; printf '%s\n' "$line"; printf '%s\n' "$line" | strip >> "$LOG"; }
logq() { printf '%s\n' "$(date +%H:%M:%S) [$STEP] $*" | strip >> "$LOG"; }   # log file only: detail the terminal does not need
step() {
  [ "$STEP" != "-" ] && logq "$STEP took $((SECONDS - STEP_T)) s"
  STEP="$1"; STEP_T=$SECONDS; echo; log "${B}== $2${N}"
}
die()  { log "${R}stopped: $*${N}"; exit 1; }
pipe() { while IFS= read -r l; do log "  $l"; done; }    # prefix and log a child's output line by line
pipeq() { while IFS= read -r l; do logq "  $l"; done; }  # the same, log file only
logfile() { [ -f "$1" ] && { logq "--- $1"; pipeq < "$1"; logq "--- end of $1"; }; }
ask()  { [ -t 0 ] || { log "${Y}no terminal to ask, so skipped: $1${N}"; return 1; }; local a; read -r -p "$(date +%H:%M:%S) [$STEP] $1 " a; REPLY="$a"; logq "answered: $a"; }

# run a command with its output shown and logged, and its exit code and duration logged
run_logged() {
  local what="$1" t=$SECONDS code; shift
  log "${D}\$ $*${N}"
  "$@" 2>&1 | pipe
  code=${PIPESTATUS[0]}
  if [ "$code" = 0 ]; then log "$what: done in $((SECONDS - t)) s"; else log "${R}$what: exit $code after $((SECONDS - t)) s${N}"; fi
  return "$code"
}

# the same, but the output goes to the log file only; its tail is shown when it fails
run_quiet() {
  local what="$1" t=$SECONDS code tmp; shift
  tmp=$(mktemp)
  log "${D}\$ $*${N}  (output in the log file)"
  "$@" > "$tmp" 2>&1; code=$?
  pipeq < "$tmp"
  if [ "$code" = 0 ]; then log "$what: done in $((SECONDS - t)) s"; else tail -n 25 "$tmp" | pipe; log "${R}$what: exit $code after $((SECONDS - t)) s${N}"; fi
  rm -f "$tmp"
  return "$code"
}

on_exit() {
  local code=$?
  if [ ${#STARTED[@]} -gt 0 ]; then
    log "stopping what this script started (pids ${STARTED[*]}); directors already running finish on their own"
    kill "${STARTED[@]}" 2>/dev/null
  fi
  [ "$STEP" != "-" ] && logq "$STEP took $((SECONDS - STEP_T)) s"
  [ "$LOG" != /dev/null ] && log "${D}exit $code after $((SECONDS)) s; full log: $LOG${N}"
}
trap on_exit EXIT
trap 'echo; log "interrupted"; exit 130' INT TERM

header() {
  log "shop.sh $*"
  log "log file $LOG"
  logq "git $(git rev-parse --short HEAD 2>/dev/null) on $(git branch --show-current 2>/dev/null), $(git status --porcelain 2>/dev/null | wc -l | tr -d ' ') uncommitted paths"
  logq "host $(uname -srm), $(os_name), bash $BASH_VERSION, user $(whoami)$([ "$(id -u)" = 0 ] && echo ' (root)')"
  logq "root $ROOT, store $STORE, python $PY, studio $STUDIO"
  logq "store/.env sets: $(grep -oE '^[A-Z_]+=' "$STORE/.env" 2>/dev/null | tr -d = | tr '\n' ' ')"
  if [ -n "${SHOP_TRACE:-}" ]; then
    exec 9>>"$LOG"; BASH_XTRACEFD=9; PS4='+ $(date +%H:%M:%S) ${FUNCNAME[0]:-main}:$LINENO '; set -x
  fi
}

up() { curl -sf -o /dev/null --max-time 3 "$1"; }

os_name() {
  if [ "$OS" = Darwin ]; then echo "macOS $(sw_vers -productVersion 2>/dev/null)"
  else ( . /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-$OS}" ); fi
}

sha256() { if command -v sha256sum > /dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -c1-64; }

# vite 8 needs node ^20.19 or >=22.12
node_ok() { "${1:-node}" -e 'const [a, b] = process.versions.node.split(".").map(Number); process.exit((a === 20 && b >= 19) || (a === 22 && b >= 12) || a > 22 ? 0 : 1)' 2>/dev/null; }

# a new enough node can sit behind an old one on PATH (a hand-installed /usr/local/bin/node);
# use it for this run and say how to fix it for good
prefer_good_node() {
  local n
  node_ok && return 0
  for n in $(type -ap node) /usr/bin/node /usr/local/bin/node "$HOME"/.nvm/versions/node/*/bin/node; do
    [ -x "$n" ] && node_ok "$n" || continue
    command -v node > /dev/null && log "${Y}node $(node --version) at $(command -v node) comes first on PATH and is too old; $n ($("$n" --version)) is new enough${N}"
    log "${Y}using $(dirname "$n") first for this run. To fix it for good, move the old one aside (e.g. sudo mv $(command -v node) $(command -v node).old, same for npm and npx) or put $(dirname "$n") first in PATH${N}"
    export PATH="$(dirname "$n"):$PATH"; hash -r
    return 0
  done
  return 1
}

# the preview render uses AgX and Blender 4's importers: 4.0 or newer
blender_ok() { "$1" --version 2>/dev/null | head -1 | grep -qE '^Blender ([4-9]|[1-9][0-9])\.'; }

# ------------------------------------------------------------------ setup

# how to install a tool on this machine: Homebrew on macOS, apt or dnf on Linux; empty when unknown
APT="sudo env DEBIAN_FRONTEND=noninteractive apt-get"
install_cmd() {
  local arch; arch=$(uname -m)
  if [ "$OS" = Darwin ]; then
    command -v brew > /dev/null || return 0
    case "$1" in
      python)  echo "brew install python@3.12" ;;
      node)    echo "brew install node" ;;
      ffmpeg)  echo "brew install ffmpeg" ;;
      blender) echo "brew install --cask blender" ;;
      chrome)  echo "brew install --cask google-chrome" ;;
    esac
  elif command -v apt-get > /dev/null; then
    case "$1" in
      curl)    echo "$APT update && $APT install -y curl ca-certificates" ;;
      # Debian 12 and Ubuntu 24.04 ship a usable python3; Ubuntu 22.04 needs deadsnakes for 3.12
      python)  echo "$APT update && { apt-cache show python3.12 > /dev/null 2>&1 || { $APT install -y software-properties-common && sudo add-apt-repository -y ppa:deadsnakes/ppa && $APT update; }; } && $APT install -y python3.12 python3.12-venv" ;;
      venv)    echo "$APT install -y $2-venv" ;;
      node)    echo "curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && $APT install -y nodejs" ;;   # apt's own node is too old for vite 8
      ffmpeg)  echo "$APT update && $APT install -y ffmpeg" ;;
      blender) [ "$arch" = x86_64 ] || { echo "$APT update && $APT install -y blender"; return 0; }
               # the distribution's blender is often 2.x; take the official 4.5 LTS build into /opt and link it onto PATH
               echo "$APT update && $APT install -y xz-utils libxi6 libxkbcommon0 libxxf86vm1 libxfixes3 libxrender1 libgl1 libsm6 libegl1 && v=\$(curl -fsSL https://download.blender.org/release/Blender4.5/ | grep -oE 'blender-4\\.5\\.[0-9]+-linux-x64' | sort -uV | tail -1) && [ -n \"\$v\" ] && { [ -x /opt/\$v/blender ] || { curl -fL -o /tmp/\$v.tar.xz https://download.blender.org/release/Blender4.5/\$v.tar.xz && sudo tar -xJf /tmp/\$v.tar.xz -C /opt; }; } && { [ ! -e /usr/local/bin/blender ] || [ -L /usr/local/bin/blender ]; } && sudo ln -sf /opt/\$v/blender /usr/local/bin/blender" ;;
      chrome)  if [ "$arch" = x86_64 ]; then echo "curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && $APT install -y /tmp/chrome.deb"
               else echo "$APT update && $APT install -y chromium"; fi ;;
    esac
  elif command -v dnf > /dev/null; then
    case "$1" in
      curl)    echo "sudo dnf install -y curl" ;;
      python)  echo "sudo dnf install -y python3.12" ;;
      node)    echo "sudo dnf install -y nodejs" ;;
      ffmpeg)  echo "sudo dnf install -y ffmpeg" ;;   # Fedora ships ffmpeg-free; RHEL needs RPM Fusion
      blender) echo "sudo dnf install -y blender" ;;
      chrome)  [ "$arch" = x86_64 ] && echo "sudo dnf install -y https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm" || echo "sudo dnf install -y chromium" ;;
    esac
  fi
}

# install a missing tool. SHOP_NO_INSTALL=1 only reports. Root runs the command without sudo;
# anyone else is asked for the sudo password once, on the terminal.
install_tool() {
  local name="$1" cmd="$2"
  if [ -z "$cmd" ]; then log "${R}$name is missing${N} and there is no package manager setup knows here (brew, apt, dnf); install it by hand"; return 1; fi
  [ "$(id -u)" = 0 ] && { cmd=${cmd//sudo -E /}; cmd=${cmd//sudo /}; }
  if [ -n "${SHOP_NO_INSTALL:-}" ]; then log "${Y}$name is missing${N}; SHOP_NO_INSTALL is set, so not running: $cmd"; return 1; fi
  case "$cmd" in *sudo*)
    if ! sudo -n true 2> /dev/null && [ ! -t 0 ]; then log "${R}$name is missing${N}; installing needs the sudo password and there is no terminal. Run: $cmd"; return 1; fi ;;
  esac
  log "${Y}$name is missing; installing${N}"
  run_logged "install $name" bash -c "$cmd"
}

blender_bin() { local b; for b in "${BLENDER:-}" /Applications/Blender.app/Contents/MacOS/Blender "$(command -v blender)" /snap/bin/blender /opt/blender/blender $(ls -d /opt/blender-4* 2>/dev/null | sort -V | tail -1 | sed 's|$|/blender|'); do [ -n "$b" ] && [ -x "$b" ] && { echo "$b"; return 0; }; done; return 1; }
chrome_bin() { local b; for b in "${CHROME:-}" "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" "$(command -v google-chrome)" "$(command -v google-chrome-stable)" "$(command -v chromium)" "$(command -v chromium-browser)"; do [ -n "$b" ] && [ -x "$b" ] && { echo "$b"; return 0; }; done; return 1; }

setup_tools() {
  local b
  command -v curl > /dev/null || install_tool curl "$(install_cmd curl)" || die "curl is needed for the rest of setup"
  if ! command -v claude > /dev/null; then
    install_tool "the Claude Code CLI" "curl -fsSL https://claude.ai/install.sh | bash"
    export PATH="$HOME/.local/bin:$PATH"   # where the installer puts it
  fi
  command -v claude > /dev/null && log "claude $(claude --version 2>/dev/null) at $(command -v claude)"
  log "system: $(os_name), $(uname -m)$([ "$(id -u)" = 0 ] && echo ', running as root')"
  if ! prefer_good_node || ! command -v npm > /dev/null; then
    install_tool "node 20.19+ or 22.12+$(command -v node > /dev/null && echo " (found $(node --version), too old for vite 8)")" "$(install_cmd node)"
    hash -r; prefer_good_node || log "${R}still no node 20.19+ or 22.12+ on this machine; the console and the design stage cannot run${N}"
  fi
  command -v node > /dev/null && log "node $(node --version), npm $(npm --version 2>/dev/null) at $(command -v node)"
  command -v ffmpeg > /dev/null && command -v ffprobe > /dev/null || install_tool ffmpeg "$(install_cmd ffmpeg)"
  command -v ffmpeg > /dev/null && log "ffmpeg at $(command -v ffmpeg): $(ffmpeg -version 2>/dev/null | head -1)"
  b=$(blender_bin)
  if [ -z "$b" ]; then install_tool "Blender 4" "$(install_cmd blender)"
  elif ! blender_ok "$b"; then install_tool "Blender 4 (found $b: $("$b" --version 2>/dev/null | head -1 || echo 'does not run'))" "$(install_cmd blender)"; fi
  hash -r
  if b=$(blender_bin) && blender_ok "$b"; then log "blender at $b: $("$b" --version 2>/dev/null | head -1)"
  elif [ -n "$b" ]; then log "${R}blender at $b is not 4.0 or newer; the preview stage will fail. Set BLENDER in store/.env to a Blender 4 binary${N}"; fi
  chrome_bin > /dev/null || install_tool "Chrome or Chromium" "$(install_cmd chrome)"
  if b=$(chrome_bin); then log "chrome at $b: $("$b" --version 2>/dev/null)"; fi
  if [ "$OS" = Linux ]; then
    [ "$(id -u)" = 0 ] && log "running as root: the design stage starts Chrome with --no-sandbox (a service user is safer)"
    [ -z "${DISPLAY:-}" ] && logq "no DISPLAY: fine, Blender renders with Cycles on the CPU and Chrome captures headless with SwiftShader WebGL"
  fi
  command -v brew > /dev/null && logq "brew $(brew --version 2>/dev/null | head -1) at $(command -v brew)"
  return 0
}

# any 3.10 to 3.15 will do (numpy and scipy ship wheels for all of them since 2026-10); 3.12 first as the best-trodden
base_python() {
  local p
  for p in python3.12 python3.13 python3.14 python3.15 python3.11 python3.10 /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 python3; do
    command -v "$p" > /dev/null 2>&1 || continue
    "$p" -c 'import sys; sys.exit(not ((3, 10) <= sys.version_info[:2] <= (3, 15)))' 2>/dev/null && { command -v "$p"; return 0; }
  done
  return 1
}

setup_python() {
  local base want stamp
  if [ ! -x "$PY" ]; then
    case "$PY" in */bin/python) ;; *) die "DAPILOT_PYTHON=$PY is not an executable; fix the store's .env or remove the line";; esac
    base=$(base_python) || { install_tool "python 3.10 to 3.15" "$(install_cmd python)"; hash -r; base=$(base_python) || die "no python 3.10 to 3.15 found"; }
    if ! run_logged "create $VENV with $base ($("$base" --version 2>&1))" "$base" -m venv "$VENV"; then
      rm -rf "$VENV"   # a failed venv leaves a python without pip behind; this run created it
      # Debian and Ubuntu ship the venv module as its own package
      [ "$OS" = Linux ] && command -v apt-get > /dev/null && install_tool "$(basename "$base")-venv" "$(install_cmd venv "$(basename "$base")")"         && run_logged "create the venv again" "$base" -m venv "$VENV" || { rm -rf "$VENV"; die "could not create $VENV"; }
    fi
  fi
  log "python: $("$PY" --version 2>&1) at $PY"
  "$PY" -c 'import sys; sys.exit(not ((3, 10) <= sys.version_info[:2] <= (3, 15)))' \
    || log "${Y}$PY is not python 3.10 to 3.15; numpy and scipy may have no wheels for it${N}"
  want=$(sha256 "$ROOT/requirements.txt")
  stamp="$(dirname "$(dirname "$PY")")/.dapilot-requirements"
  if [ "$(cat "$stamp" 2>/dev/null)" = "$want" ] && "$PY" -c 'import trimesh, scipy, networkx, jsonschema, fastapi, uvicorn, fal_client' 2>/dev/null; then
    log "python packages match requirements.txt"
  else
    log "installing python packages from requirements.txt (changed since the last install, or first run)"
    run_quiet "pip install" "$PY" -m pip install --disable-pip-version-check -r "$ROOT/requirements.txt" || die "pip install failed; the lines above say why"
    echo "$want" > "$stamp"
  fi
  "$PY" -m pip freeze --disable-pip-version-check 2>/dev/null | pipeq
}

setup_frontend() {
  local nm="$ROOT/frontend/node_modules"
  command -v npm > /dev/null || { log "${Y}no npm: the console and studio cannot run, so the design stage cannot either${N}"; return 0; }
  node_ok || { log "${Y}node $(node --version) is too old for vite 8 (needs 20.19+ or 22.12+); skipping npm install${N}"; return 0; }
  if [ -f "$nm/.package-lock.json" ] && [ ! "$ROOT/frontend/package-lock.json" -nt "$nm/.package-lock.json" ] && [ ! "$ROOT/frontend/package.json" -nt "$nm/.package-lock.json" ]; then
    log "frontend packages match package-lock.json"
  else
    run_quiet "npm install" npm --prefix "$ROOT/frontend" install --no-fund --no-audit || die "npm install failed; the lines above say why"
  fi
}

# store/.env and store/config.json from their templates; the values themselves are yours
setup_store() {
  local v empty=()
  mkdir -p "$STORE/reference"
  if [ ! -f "$STORE/.env" ]; then
    cp "$ROOT/plugins/dapilot/templates/.env.example" "$STORE/.env" && log "${Y}created $STORE/.env from the plugin's .env.example${N}"
  else
    log "store/.env present ($(grep -cE '^[A-Z_]+=.' "$STORE/.env") values set)"
  fi
  if [ ! -f "$STORE/config.json" ]; then
    cp "$ROOT/plugins/dapilot/templates/config.example.json" "$STORE/config.json" && log "created $STORE/config.json from the plugin's config.example.json"
  else
    log "store/config.json present"
  fi
  for v in DAPI_API_URL DAPI_API_KEY ETSY_KEYSTRING ETSY_SHARED_SECRET; do [ -n "${!v:-}" ] || empty+=("$v"); done
  [ -n "${FAL_KEY:-}${FAL_API_KEY:-}" ] || empty+=(FAL_KEY)
  if [ ${#empty[@]} -gt 0 ]; then
    log "${R}store/.env has no value for: ${empty[*]}${N}"
    log "fill them in ($STORE/.env, the comments say where each comes from), then run this again"
    return 1
  fi
  if [ -f "$STORE/reference/pricing-policy.json" ]; then
    log "pricing policy present: $("$PY" -c 'import json,sys; p=json.load(open(sys.argv[1])); print(", ".join(f"{k} {p.get(k)}" for k in ("cost_basis", "markup_pct", "seller_region", "currency")) + ", vat " + str((p.get("vat") or {}).get("rate")))' "$STORE/reference/pricing-policy.json" 2>&1)"
  else
    cp "$ROOT/plugins/dapilot/templates/pricing-policy.example.json" "$STORE/reference/pricing-policy.json" || return 1
    log "${Y}created reference/pricing-policy.json from the plugin's pricing-policy.example.json: 3DAPI's own policy (EU seller, 21% VAT, 50% markup, full-plate cost).${N}"
    log "${Y}review it before approving any price; every price proposal and the break-even floor come from it${N}"
  fi
}

# store/reference/: what Etsy can tell us, fetched once. Needs the Etsy keys, and the token for the shop's ids.
setup_reference() {
  local ref="$STORE/reference"
  [ -n "${ETSY_KEYSTRING:-}" ] && [ -n "${ETSY_SHARED_SECRET:-}" ] || { log "${Y}no Etsy keys; skipping the taxonomy and listing defaults${N}"; return 0; }
  local tax
  for tax in seller buyer; do
    if [ -s "$ref/etsy_${tax}_taxonomy.json" ]; then
      log "etsy $tax taxonomy present ($(wc -c < "$ref/etsy_${tax}_taxonomy.json" | tr -d ' ') bytes)"
    else
      run_logged "etsy $tax taxonomy" "$PY" - "$SCRIPTS" "$tax" "$ref/etsy_${tax}_taxonomy.json" <<'PYEOF'
import json, sys
sys.path.insert(0, sys.argv[1])
import etsy_search
quota = {}
data = etsy_search.get(f"/{sys.argv[2]}-taxonomy/nodes", {}, etsy_search.api_key(), quota)
json.dump(data, open(sys.argv[3], "w"))
print(f"{len(data.get('results') or [])} top-level categories written to {sys.argv[3]}; {quota.get('remaining_today')} Etsy calls left today")
PYEOF
    fi
  done
  if [ -f "$ref/etsy-listing.json" ]; then
    log "etsy listing defaults present: $("$PY" -c 'import json,sys; d=json.load(open(sys.argv[1])); m=[k for k in ("shipping_profile_id","return_policy_id","readiness_state_id") if not d.get(k)]; print("missing " + ", ".join(m) + ": activating a listing will fail" if m else "shipping, return and readiness ids set")' "$ref/etsy-listing.json")"
    return 0
  fi
  [ -f "$STORE/etsy_token.json" ] || { log "${Y}no Etsy token, so no listing defaults yet; rerun setup after the sign-in${N}"; return 0; }
  log "writing reference/etsy-listing.json from the template and the shop's own profiles"
  "$PY" "$SCRIPTS/etsy_listing.py" profiles > "$ref/.profiles.json" 2>&1; local code=$?
  pipeq < "$ref/.profiles.json"
  [ $code = 0 ] || { tail -n 5 "$ref/.profiles.json" | pipe; log "${R}etsy_listing.py profiles failed (exit $code); listing defaults not written${N}"; rm -f "$ref/.profiles.json"; return 0; }
  "$PY" - "$ROOT/plugins/dapilot/templates/etsy-listing.example.json" "$ref/.profiles.json" "$ref/etsy-listing.json" <<'PYEOF' 2>&1 | pipe
import json, sys
doc, prof = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
doc["comment"] = f"Shop-wide listing choices for shop {prof.get('shop_id')}, written by shop.sh setup from the template and etsy_listing.py profiles."
for key, field in (("shipping_profiles", "shipping_profile_id"), ("return_policies", "return_policy_id"),
                   ("readiness_states", "readiness_state_id")):
    options = prof.get(key) or []
    if len(options) == 1:
        doc[field] = options[0]["id"]
        print(f"{field} = {options[0]['id']} (the shop's only one: {options[0]})")
    elif options:
        print(f"{field}: the shop has {len(options)}:", flush=True)
        for i, o in enumerate(options, 1):
            print(f"  {i}. {o}", flush=True)
        try:  # the heredoc is stdin, so ask on the terminal itself
            with open("/dev/tty", "r+") as tty:
                tty.write(f"which {field} should every listing use? [1-{len(options)}, empty to leave it unset] ")
                tty.flush()
                pick = tty.readline().strip()
        except OSError:
            pick = ""
        if pick.isdigit() and 1 <= int(pick) <= len(options):
            doc[field] = options[int(pick) - 1]["id"]
            print(f"{field} = {doc[field]} (picked {pick})")
        else:
            print(f"{field} left empty: a draft works, activating fails until it is set in etsy-listing.json")
    else:
        print(f"{field}: the shop has none; create one on Etsy, then set it in etsy-listing.json")
partners = prof.get("production_partners") or []
doc["production_partner_ids"] = [p["id"] for p in partners]
print(f"production_partner_ids = {doc['production_partner_ids']}")
json.dump(doc, open(sys.argv[3], "w"), indent=1)
print(f"wrote {sys.argv[3]}; who_made, when_made, quantity and the rest come from the template: review them")
PYEOF
  rm -f "$ref/.profiles.json"
}

claude_signed_in() { [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}${ANTHROPIC_API_KEY:-}" ] || claude auth status 2>/dev/null | grep -q '"loggedIn": *true'; }

etsy_scope() { "$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("scope") or "")' "$STORE/etsy_token.json" 2>/dev/null; }

setup_signins() {
  local tok="$STORE/etsy_token.json" scope s
  # Claude: workers are claude -p
  if ! command -v claude > /dev/null; then :
  elif claude_signed_in; then
    log "claude signed in: $(claude auth status 2>/dev/null | "$PY" -c 'import json,sys; j=json.load(sys.stdin); print(" ".join(str(j.get(k)) for k in ("authMethod", "email", "subscriptionType") if j.get(k)))' 2>/dev/null)$([ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] && echo ", CLAUDE_CODE_OAUTH_TOKEN set")$([ -n "${ANTHROPIC_API_KEY:-}" ] && echo ", ANTHROPIC_API_KEY set: workers bill to the API")"
  else
    log "${Y}claude is not signed in${N}; workers need it (or CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY in store/.env)"
    ask "sign in now? [y/N]" && [ "$REPLY" = y ] && run_logged "claude sign-in" claude auth login
  fi
  # Etsy: research reads, publish writes
  scope=$(etsy_scope)
  if [ -f "$tok" ] && [ -z "$(for s in $ETSY_SCOPES; do case " $scope " in *" $s "*) ;; *) echo "$s";; esac; done)" ]; then
    log "etsy token present, scope: $scope"
  else
    if [ -f "$tok" ]; then log "${Y}etsy token scope is '$scope'; publishing needs $ETSY_SCOPES${N}"; else log "${Y}no Etsy token yet${N}"; fi
    log "the sign-in prints a link: open it in a browser signed in to the shop (any computer), approve, then paste back the address the browser ends on"
    log "the app at https://www.etsy.com/developers/your-apps must list http://localhost:3003/oauth/redirect as a callback"
    if ask "sign in to Etsy now? [y/N]" && [ "$REPLY" = y ]; then
      [ -f "$tok" ] && cp "$tok" "$STORE/etsy_token.bak-$STAMP.json" && log "kept the old token as etsy_token.bak-$STAMP.json"
      local nb=()   # no display: nothing to open a browser on, so print the link and take the pasted address
      [ "$OS" = Linux ] && [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && nb=(--no-browser)
      run_logged "etsy sign-in" "$PY" "$SCRIPTS/etsy_auth.py" --env "$STORE/.env" login --scopes "$ETSY_SCOPES" ${nb[@]+"${nb[@]}"}
    fi
  fi
  # Instagram: only when a platform is enabled for posting
  if "$PY" -c 'import json,sys; c=json.load(open(sys.argv[1])); sys.exit(not any(v.get("enabled") for v in ((c.get("social") or {}).get("platforms") or {}).values()))' "$STORE/config.json" 2>/dev/null; then
    if "$PY" "$SCRIPTS/social_post.py" session status > /dev/null 2>&1; then
      log "social sessions usable: $("$PY" "$SCRIPTS/social_post.py" session status 2>/dev/null | "$PY" -c 'import json,sys; print(", ".join(" ".join(str(s.get(k) or "") for k in ("platform", "state", "note")) for s in json.load(sys.stdin).get("sessions") or []))')"
    elif [ -n "${INSTAGRAM_ACCESS_TOKEN:-}${INSTAGRAM_APP_ID:-}" ]; then
      run_quiet "instagram session" "$PY" "$SCRIPTS/social_post.py" session login
    else
      log "${Y}social posting is enabled in config.json but no session is usable and store/.env has no INSTAGRAM_ACCESS_TOKEN${N}"
    fi
  else
    log "no social platform enabled in config.json; skipping social sign-ins"
  fi
}

do_setup() {
  local t=$SECONDS
  log "tools";          setup_tools
  log "python";         setup_python
  log "frontend";       setup_frontend
  log "store files";    setup_store || return 2
  log "sign-ins";       setup_signins
  log "store reference"; setup_reference
  logq "setup took $((SECONDS - t)) s"
}

# ------------------------------------------------------------------ steps

do_check() {
  "$PY" "$INSPECT" check "$@" 2>&1 | pipe
  return "${PIPESTATUS[0]}"
}

services() {
  if up "$API/health"; then
    log "job API already answering on :3100: $(curl -s --max-time 3 "$API/health")"
  else
    log "starting the job API on :3100 (log runs/server.log)"
    "$PY" -m uvicorn server.app:app --port 3100 >> "$ROOT/runs/server.log" 2>&1 &
    STARTED+=($!)
    for _ in $(seq 1 40); do up "$API/health" && break; sleep 0.5; done
    if ! up "$API/health"; then tail -n 30 "$ROOT/runs/server.log" | pipe; die "the job API did not come up; the tail of runs/server.log is above"; fi
    log "job API up (pid ${STARTED[${#STARTED[@]}-1]}): $(curl -s --max-time 3 "$API/health")"
  fi
  if up "$STUDIO"; then
    log "studio already answering at $STUDIO"
  else
    log "starting the console and studio on :8081 (log runs/frontend.log); design captures need it"
    ( cd frontend && exec npm run dev >> "$ROOT/runs/frontend.log" 2>&1 ) &
    STARTED+=($!)
    for _ in $(seq 1 60); do up "$STUDIO" && break; sleep 0.5; done
    if up "$STUDIO"; then log "studio up at $STUDIO"; else tail -n 20 "$ROOT/runs/frontend.log" | pipe; log "${Y}studio did not answer; the design stage will fail its captures (runs/frontend.log, tail above)${N}"; fi
  fi
}

# run_intake.py: progress on stderr (logged live), one JSON summary on stdout
intake() {
  local run="$1"; shift
  local summary="$ROOT/runs/$run.summary.json" t=$SECONDS
  log "director: run_intake.py --run $run $*"
  log "every worker's raw output lands in runs/$run/workers/, the ledger in runs/$run/state.json"
  "$PY" "$SCRIPTS/run_intake.py" --run "$run" "$@" 2>&1 1>"$summary" | pipe
  local code=${PIPESTATUS[0]}
  log "director exited $code after $((SECONDS - t)) s"
  if [ -s "$summary" ]; then
    "$PY" -c 'import json,sys; s=json.load(open(sys.argv[1])); print(f"run {s.get(\"run\")}: {s.get(\"counts\")}, {s.get(\"workers_dispatched\")} workers, {s.get(\"usd_workers\")} USD, {s.get(\"seconds\")} s, billing {s.get(\"billing\")}"); print("report " + str(s.get("report")))' "$summary" 2>/dev/null | pipe
    logfile "$summary"
  fi
  logfile "$ROOT/runs/$run/report.md"
  return "$code"
}

show_status() { "$PY" "$INSPECT" status "$@" 2>&1 | pipe; }

items_of_run() { "$PY" -c 'import json,sys; print("\n".join(json.load(open(sys.argv[1]))["items"]))' "$ROOT/runs/$1/state.json" 2>/dev/null; }

# follow a director the server started, until its process ends
follow() {
  local run="$1" f="$ROOT/runs/$1/director.log" t=$SECONDS
  log "following director $run ($f)"
  for _ in $(seq 1 20); do [ -f "$f" ] && break; sleep 0.5; done
  tail -n +1 -f "$f" 2>/dev/null | pipe &
  local tailer=$!
  while pgrep -f "run_intake.py --run $run" > /dev/null; do sleep 3; done
  sleep 1; kill "$tailer" 2>/dev/null; wait "$tailer" 2>/dev/null
  log "director $run finished after $((SECONDS - t)) s"
  logfile "$ROOT/runs/$run/report.md"
}

# POST /jobs/{id}/price: the same gate the console uses, with price_candidates.py --check behind it
approve() {
  local item="$1" price="${2:-}" reason="${3:-}" by
  by="$(git config user.name 2>/dev/null || whoami) (shop.sh)"
  local body
  body=$("$PY" -c 'import json,sys; p,r,b=sys.argv[1:4]; print(json.dumps({"price": float(p) if p else None, "reason": r or None, "by": b}))' "$price" "$reason" "$by")
  logq "POST $API/jobs/$item/price $body"
  local resp code
  resp=$(curl -s -w '\n%{http_code}' -X POST -H 'content-type: application/json' -d "$body" "$API/jobs/$item/price")
  code=$(tail -n1 <<<"$resp"); resp=$(sed '$d' <<<"$resp")
  logq "response $code: $resp"
  if [ "$code" != 200 ]; then
    log "${R}price refused ($code): $("$PY" -c 'import json,sys; print(json.loads(sys.stdin.read()).get("detail"))' <<<"$resp" 2>/dev/null || echo "$resp")${N}"
    return 1
  fi
  APPROVED_RUN=$("$PY" -c 'import json,sys; print(json.loads(sys.stdin.read()).get("run") or "")' <<<"$resp")
  log "${G}approved ${price:-the proposal} for ${item:0:12} as $by${N}${APPROVED_RUN:+; the server started director $APPROVED_RUN}"
}

full_hash() { ls -d "$ROOT"/work/"$1"* 2>/dev/null | head -1 | xargs -n1 basename 2>/dev/null; }

publish_note() {
  "$PY" - "$STORE/config.json" <<'EOF'
import json, sys
c = json.load(open(sys.argv[1]))
d, p = c.get("design") or {}, c.get("publish") or {}
print(f"approving starts: design (fal photos and clip, up to {d.get('usd_cap_per_item')} USD per item), copy (worker tokens),")
print("then publish: " + ("the Etsy listing goes ACTIVE and public (0.20 USD fee)" if p.get("auto_activate", True) else "an Etsy draft only (auto_activate off)")
      + (" and the social posts are sent" if p.get("auto_post", True) else ", no social posts (auto_post off)"))
EOF
}

# the gate for every item of a run whose price waits on a person
gate() {
  local run="$1" no_approve="$2" item waiting=()
  for item in $(items_of_run "$run"); do
    [ -f "work/$item/price.json" ] && [ ! -f "work/$item/price.approved.json" ] && waiting+=("$item")
  done
  if [ ${#waiting[@]} -eq 0 ]; then log "no item of this run is waiting on a price"; return; fi
  log "${#waiting[@]} item(s) wait for a person to approve the price"
  publish_note | pipe
  for item in "${waiting[@]}"; do
    echo
    "$PY" "$INSPECT" gate "$item" 2>&1 | pipe
    if [ "$no_approve" = 1 ] || [ ! -t 0 ]; then
      log "left for a person: scripts/dev/shop.sh approve ${item:0:12} [price] [reason], or the console at http://localhost:8081"
      continue
    fi
    while true; do
      ask "[a]pprove the proposal, [o]verride the price, [s]kip:" || break
      case "$REPLY" in
        a|A) ask "type 'approve' to confirm (this can make the listing public):" && [ "$REPLY" = approve ] || { log "not confirmed"; continue; }
             approve "$item" && { [ -n "$APPROVED_RUN" ] && follow "$APPROVED_RUN"; break; } ;;
        o|O) ask "price:"; local p="$REPLY"; ask "reason (needed unless the price is one of the candidates):"; local r="$REPLY"
             ask "type 'approve' to confirm $p (this can make the listing public):" && [ "$REPLY" = approve ] || { log "not confirmed"; continue; }
             approve "$item" "$p" "$r" && { [ -n "$APPROVED_RUN" ] && follow "$APPROVED_RUN"; break; } ;;
        s|S) log "skipped ${item:0:12}; approve later with scripts/dev/shop.sh approve ${item:0:12}"; break ;;
      esac
    done
  done
}

# ------------------------------------------------------------------ commands

cmd="${1:-help}"; shift || true
case "$cmd" in
  setup)
    header setup "$@"
    step "1/2 setup" "tools, python, frontend, store files, sign-ins"
    do_setup; [ $? -eq 2 ] && exit 2
    step "2/2 check" "is everything ready, per stage"
    do_check "$@"; code=$?
    [ $code -eq 0 ] && log "${G}ready: scripts/dev/shop.sh run <model file>${N}"
    exit "$code" ;;

  check)
    header check "$@"
    STEP=check; do_check "$@"; exit $? ;;

  status)
    LOG=/dev/null; STEP=status; show_status "$@" ;;

  approve)
    header approve "$@"
    [ -n "${1:-}" ] || die "usage: shop.sh approve <hash> [price] [reason]"
    item=$(full_hash "$1"); [ -n "$item" ] || die "no work dir starts with $1"
    STEP="gate"; services
    "$PY" "$INSPECT" gate "$item" 2>&1 | pipe
    publish_note | pipe
    approve "$item" "${2:-}" "${3:-}" || exit 1
    [ -n "$APPROVED_RUN" ] && follow "$APPROVED_RUN"
    STEP=done; show_status "$item" ;;

  resume)
    header resume "$@"
    step "1/3 setup" "install what is missing, check the rest"
    do_setup; [ $? -eq 2 ] && die "fix store/.env first"
    do_check; [ $? -eq 2 ] && die "fix the MISSING lines first"
    step "2/3 services" "job API and studio"; services
    step "3/3 intake" "every work dir with a stage left"
    run="resume-$STAMP"; intake "$run" --resume
    STEP=done; show_status $(items_of_run "$run") ;;

  run)
    header run "$@"
    src="${1:-}"; [ -n "$src" ] || die "usage: shop.sh run <file|folder> [--no-approve] [--dry-run]"
    no_approve=0; dry=0
    for a in "${@:2}"; do case "$a" in --no-approve) no_approve=1;; --dry-run) dry=1;; *) die "unknown option $a";; esac; done
    [ -e "$src" ] || die "$src does not exist"

    step "1/6 setup" "tools, python, frontend, store files, sign-ins, then the check"
    do_setup; [ $? -eq 2 ] && die "fix store/.env, then run again"
    do_check; code=$?
    [ $code -eq 2 ] && die "fix the MISSING lines above, then run again"
    if [ $code -eq 1 ]; then
      ask "some stages are blocked; the run will stop at the first of them. continue? [y/N]" && [ "$REPLY" = y ] || die "not continuing"
    fi

    step "2/6 services" "job API (:3100) and studio (:8081)"
    services

    step "3/6 intake" "analyze, quote, preview, research, price"
    if [ -f "$src" ]; then
      folder="$ROOT/intake/shop-$STAMP"; mkdir -p "$folder"; cp "$src" "$folder/" || die "could not copy $src"
      log "copied $src to intake/shop-$STAMP/ (the original is left where it is)"
    else
      folder="$src"
    fi
    log "files: $(ls "$folder" | grep -iE '\.(stl|3mf|obj|step|stp)$' | tr '\n' ' ')"
    "$PY" - "$folder" <<'PYEOF' | pipeq
import hashlib, sys
from pathlib import Path
for f in sorted(Path(sys.argv[1]).iterdir()):
    if f.suffix.lower() in {".stl", ".3mf", ".obj", ".step", ".stp"}:
        wd = Path("work") / hashlib.sha256(f.read_bytes()).hexdigest()
        print(f"{f.name}: {f.stat().st_size} bytes, sha256 {wd.name}, work dir {'exists' if wd.exists() else 'new'}")
PYEOF
    # an item whose price a person already approved goes on to paid and public stages without asking again
    approved=$("$PY" - "$folder" <<'PYEOF'
import hashlib, sys
from pathlib import Path
for f in sorted(Path(sys.argv[1]).iterdir()):
    if f.suffix.lower() in {".stl", ".3mf", ".obj", ".step", ".stp"}:
        wd = Path("work") / hashlib.sha256(f.read_bytes()).hexdigest()
        if (wd / "price.approved.json").exists() and not (wd / "published.json").exists():
            print(f"{wd.name[:12]}  {f.name}")
PYEOF
)
    if [ -n "$approved" ] && [ $dry = 0 ]; then
      log "${Y}price already approved, so this run goes on into design, copy and publish without another gate:${N}"
      printf '%s\n' "$approved" | pipe
      publish_note | pipe
      ask "go ahead with those? [y/N]" && [ "$REPLY" = y ] || die "not continuing; --dry-run shows what would run"
    fi
    run="shop-$STAMP"; [ $dry = 1 ] && run="dryrun-$STAMP"
    if [ $dry = 1 ]; then
      log "dry run: the director shows its first pass and launches nothing"
      intake "$run" "$folder" --dry-run
      log "what the director would dispatch:"; pipe < "$ROOT/runs/$run.summary.json"
      show_status $(items_of_run "$run"); exit 0
    fi
    intake "$run" "$folder" || log "${Y}the director reported failures; the status below says which${N}"

    step "4/6 where it is" "after the unattended stages"
    show_status $(items_of_run "$run")

    step "5/6 price gate" "a person approves the price; the pipeline spends and publishes only after this"
    gate "$run" "$no_approve"

    step "6/6 result" "where every item of this run ended"
    show_status $(items_of_run "$run")
    for item in $(items_of_run "$run"); do
      [ -f "work/$item/published.json" ] && log "published ${item:0:12}: $("$PY" -c 'import json,sys; p=json.load(open(sys.argv[1])); print(p.get("url") or p)' "work/$item/publish.json" 2>/dev/null)"
      logq "files in work/$item: $(ls "work/$item" 2>/dev/null | tr '\n' ' ')"
    done
    log "run report: runs/$run/report.md" ;;

  *)
    sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
