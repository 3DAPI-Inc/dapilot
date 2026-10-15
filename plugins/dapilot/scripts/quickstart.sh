#!/usr/bin/env bash
# The front door of /dapilot:quickstart on macOS and Linux (and the relay to quickstart.bat on Windows).
#
#   quickstart.sh [file or folder]
#
# quickstart.py is Python, so something has to find a Python before it can run. This does that and
# nothing else: it looks for an interpreter the pipeline's packages support (3.10 to 3.13), installs
# one when there is none (the system's package manager when it can, else the official standalone
# CPython build unpacked under the store, no root needed), and hands over to quickstart.py with the
# same arguments. Everything after that (the venv, Blender, Chrome, ffmpeg, the accounts) is
# quickstart.py's job.
#
# DAPILOT_BOOTSTRAP_ONLY=1 prints the interpreter it would use and exits: a check, not a run.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONUTF8=1   # utf-8 in and out for every script below, whatever the locale or code page

case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*)
    # Git Bash on Windows: the .bat knows winget and the py launcher; let it do the work.
    BAT="$HERE/quickstart.bat"
    command -v cygpath >/dev/null 2>&1 && BAT="$(cygpath -w "$BAT")"
    exec cmd.exe //c "\"$BAT\" $*"
    ;;
esac

MIN=10; MAX=13   # 3.10 to 3.13: the versions with wheels for trimesh, scipy and networkx
PY_HOME="${DAPILOT_STORE_DIR:-$HOME/.dapilot}/python"   # where a Python we installed ourselves lives
PBS_API="https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"

usable() {  # $1: an executable. True when it is a CPython 3.10 to 3.13 that actually runs.
  [ -n "${1:-}" ] && command -v "$1" >/dev/null 2>&1 &&
  "$1" -c "import sys; sys.exit(0 if (3, $MIN) <= sys.version_info[:2] <= (3, $MAX) else 1)" >/dev/null 2>&1
}

find_python() {
  local c
  for c in "${DAPILOT_PYTHON:-}" "$PY_HOME/bin/python3" python3.13 python3.12 python3.11 python3.10 python3 python \
           /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /usr/local/bin/python3.13 /usr/local/bin/python3.12 \
           /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 /Library/Frameworks/Python.framework/Versions/3.12/bin/python3; do
    if usable "$c"; then command -v "$c"; return 0; fi
  done
  return 1
}

standalone_python() {
  # The official standalone CPython 3.12 build, unpacked under the store: no root, no package manager,
  # the same on a fresh Mac, a Linux box without sudo, or a locked-down laptop. The archive is verified
  # against the release's SHA256SUMS before it is unpacked.
  local os arch triple json asset url sums sum tmp have
  case "$(uname -s)" in Darwin) os="apple-darwin" ;; Linux) os="unknown-linux-gnu" ;; *) return 1 ;; esac
  case "$(uname -m)" in arm64|aarch64) arch="aarch64" ;; x86_64|amd64) arch="x86_64" ;; *) echo "No standalone Python for $(uname -m)."; return 1 ;; esac
  triple="$arch-$os"
  command -v curl >/dev/null 2>&1 || { echo "curl is needed to download Python."; return 1; }
  command -v tar >/dev/null 2>&1 || { echo "tar is needed to unpack Python."; return 1; }
  echo "Downloading Python 3.12 (about 30 MB)…"
  json="$(curl -fsSL -m 30 "$PBS_API")" || { echo "Could not reach GitHub to fetch Python."; return 1; }
  # the download URL carries the '+' of the version as %2B; SHA256SUMS and the archive name carry it literally
  url="$(printf '%s' "$json" | grep -o '"browser_download_url": *"[^"]*cpython-3\.12\.[0-9]*\(+\|%2B\)[0-9]*-'"$triple"'-install_only\.tar\.gz"' | head -1 | sed 's/.*"\(https[^"]*\)"/\1/')"
  [ -n "$url" ] || { echo "No Python 3.12 build for $triple in the latest release."; return 1; }
  asset="${url##*/}"; asset="${asset//%2B/+}"
  sums="$(printf '%s' "$json" | grep -o '"browser_download_url": *"[^"]*/SHA256SUMS"' | head -1 | sed 's/.*"\(https[^"]*\)"/\1/')"
  tmp="$(mktemp -d)"
  curl -fsSL -m 600 -o "$tmp/$asset" "$url" || { echo "The download failed."; rm -rf "$tmp"; return 1; }
  if [ -n "$sums" ]; then
    sum="$(curl -fsSL -m 60 "$sums" | grep " $asset\$" | awk '{print $1}')"
    if command -v shasum >/dev/null 2>&1; then have="$(shasum -a 256 "$tmp/$asset" | awk '{print $1}')"; else have="$(sha256sum "$tmp/$asset" | awk '{print $1}')"; fi
    [ -n "$sum" ] && [ "$sum" = "$have" ] || { echo "The download did not match its checksum; not installed."; rm -rf "$tmp"; return 1; }
  fi
  mkdir -p "$(dirname "$PY_HOME")" && rm -rf "$PY_HOME.new" && mkdir -p "$PY_HOME.new" &&
    tar -xzf "$tmp/$asset" -C "$PY_HOME.new" && rm -rf "$PY_HOME" && mv "$PY_HOME.new/python" "$PY_HOME" && rm -rf "$PY_HOME.new" "$tmp" ||
    { echo "Could not unpack Python into $PY_HOME."; rm -rf "$PY_HOME.new" "$tmp"; return 1; }
  echo "Python $("$PY_HOME/bin/python3" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])') installed under $PY_HOME."
}

install_python() {
  case "$(uname -s)" in
    Darwin)
      if command -v brew >/dev/null 2>&1; then
        echo "Installing Python 3.12 with Homebrew (a minute or two)…"
        brew install -q python@3.12 >/dev/null 2>&1 || brew install python@3.12 >/dev/null 2>&1 || standalone_python
      else
        standalone_python   # a fresh Mac has no Homebrew and only Apple's 3.9
      fi ;;
    Linux)
      if command -v apt-get >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        echo "Installing Python 3 with apt…"
        sudo apt-get install -y -q python3 python3-venv python3-pip >/dev/null 2>&1 || standalone_python
      elif command -v dnf >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        echo "Installing Python 3 with dnf…"
        sudo dnf install -y -q python3 python3-pip >/dev/null 2>&1 || standalone_python
      else
        standalone_python   # no package manager, or no sudo without a password: install under the store instead
      fi ;;
    *)
      echo "Python 3.10 to 3.13 is needed. Install it from https://www.python.org/downloads/, then run quickstart again."
      return 1 ;;
  esac
}

PY="$(find_python)" || { install_python && PY="$(find_python)"; } || PY=""

if [ -z "$PY" ]; then
  # Nothing in the supported range even after trying. Any Python 3 can still run quickstart.py
  # itself, which then reports the version problem in its own words; otherwise stop here.
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c "import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)" >/dev/null 2>&1; then
      PY="$(command -v "$c")"; break
    fi
  done
  [ -n "$PY" ] || { echo "No Python 3 was found, so setup cannot start. Install Python 3.12 from https://www.python.org/downloads/ and run quickstart again."; exit 2; }
fi

if [ "${DAPILOT_BOOTSTRAP_ONLY:-}" = "1" ]; then
  echo "$PY $("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
  exit 0
fi

exec "$PY" "$HERE/quickstart.py" "$@"
