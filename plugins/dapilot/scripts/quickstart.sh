#!/usr/bin/env bash
# The front door of /dapilot:quickstart on macOS and Linux (and the relay to quickstart.bat on Windows).
#
#   quickstart.sh [file or folder]
#
# quickstart.py is Python, so something has to find a Python before it can run. This does that and
# nothing else: it looks for an interpreter the pipeline's packages support (3.10 to 3.13), installs
# one with the system's package manager when there is none, and hands over to quickstart.py with the
# same arguments. Everything after that (the venv, Blender, Chrome, ffmpeg, the accounts) is
# quickstart.py's job.
#
# DAPILOT_BOOTSTRAP_ONLY=1 prints the interpreter it would use and exits: a check, not a run.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*)
    # Git Bash on Windows: the .bat knows winget and the py launcher; let it do the work.
    BAT="$HERE/quickstart.bat"
    command -v cygpath >/dev/null 2>&1 && BAT="$(cygpath -w "$BAT")"
    exec cmd.exe //c "\"$BAT\" $*"
    ;;
esac

MIN=10; MAX=13   # 3.10 to 3.13: the versions with wheels for trimesh, scipy and networkx

usable() {  # $1: an executable. True when it is a CPython 3.10 to 3.13 that actually runs.
  [ -n "${1:-}" ] && command -v "$1" >/dev/null 2>&1 &&
  "$1" -c "import sys; sys.exit(0 if (3, $MIN) <= sys.version_info[:2] <= (3, $MAX) else 1)" >/dev/null 2>&1
}

find_python() {
  local c
  for c in "${DAPILOT_PYTHON:-}" python3.13 python3.12 python3.11 python3.10 python3 python \
           /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /usr/local/bin/python3.13 /usr/local/bin/python3.12 \
           /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 /Library/Frameworks/Python.framework/Versions/3.12/bin/python3; do
    if usable "$c"; then command -v "$c"; return 0; fi
  done
  return 1
}

install_python() {
  case "$(uname -s)" in
    Darwin)
      if command -v brew >/dev/null 2>&1; then
        echo "Installing Python 3.12 with Homebrew (a minute or two)…"
        brew install -q python@3.12 >/dev/null 2>&1 || brew install python@3.12 >/dev/null 2>&1
      else
        echo "Python 3.10 to 3.13 is needed and Homebrew is not installed. Install Python from https://www.python.org/downloads/ (3.12 is a good choice), then run quickstart again."
        return 1
      fi ;;
    Linux)
      if command -v apt-get >/dev/null 2>&1; then
        echo "Installing Python 3 with apt (you may be asked for your password)…"
        sudo -n true 2>/dev/null && sudo apt-get install -y -q python3 python3-venv python3-pip >/dev/null 2>&1 ||
          { echo "Run: sudo apt-get install -y python3 python3-venv python3-pip   then run quickstart again."; return 1; }
      elif command -v dnf >/dev/null 2>&1; then
        echo "Installing Python 3 with dnf (you may be asked for your password)…"
        sudo -n true 2>/dev/null && sudo dnf install -y -q python3 python3-pip >/dev/null 2>&1 ||
          { echo "Run: sudo dnf install -y python3 python3-pip   then run quickstart again."; return 1; }
      else
        echo "Python 3.10 to 3.13 is needed. Install it with your package manager or from https://www.python.org/downloads/, then run quickstart again."
        return 1
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
