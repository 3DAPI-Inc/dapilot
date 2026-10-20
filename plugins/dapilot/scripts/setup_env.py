#!/usr/bin/env python3
"""The environment the pipeline needs, set up the same way on macOS, Linux and Windows.

    setup_env.py [--no-install]      one line per item; exit 0 when everything is there, 1 when something is missing

What it does, in order, and says in one line each:
  Python    an interpreter with the plugin's packages. $DAPILOT_PYTHON when it is set and works; else a
            venv under the store (~/.dapilot/venv) made from this Python, with requirements.txt installed
            when they changed. The path is written to the store's .env as DAPILOT_PYTHON.
  Chrome    found at the usual places or on PATH, else installed with the system's package manager
            (winget on Windows, brew on macOS, apt or dnf on Linux). The path is written as CHROME.
            Chromium counts. Every render, the preview drafts and the product stills, is WebGL in
            headless Chrome from the studio page the plugin ships; Blender is not needed since 2026-10-20.
  ffmpeg    the same; it has to be on PATH, which a fresh terminal gets after the install.

Installing may ask the person for permission on their screen: winget raises Windows' own elevation
prompt for a machine-wide package, apt needs sudo. When the script cannot ask (no terminal, no
cached sudo), it prints the one command to run by hand and goes on. Standard library only.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from store_path import store_dir  # noqa: E402
import integrate  # noqa: E402  read_env / write_env: the store's .env, edited in place

OS = platform.system()          # Darwin, Linux, Windows
REQUIREMENTS = HERE.parent / "requirements.txt"
IMPORT_CHECK = "import trimesh, scipy, networkx, numpy, jsonschema, fal_client"
CHROME_URL, FFMPEG_URL = "https://www.google.com/chrome/", "https://ffmpeg.org/download.html"


def say(line: str) -> None:
    print(line, flush=True)


def run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, stdin=subprocess.DEVNULL, text=True, capture_output=True, **kw)


# --------------------------------------------------------------------------- python

def python_ok(exe: str | None) -> bool:
    if not exe or not Path(exe).exists():
        return False
    return run([exe, "-c", IMPORT_CHECK]).returncode == 0


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if OS == "Windows" else "bin/python")


def setup_python(store: Path, install: bool) -> str | None:
    pinned = os.environ.get("DAPILOT_PYTHON")
    if python_ok(pinned):
        say(f"Python ready: {run([pinned, '--version']).stdout.strip()}")
        return pinned
    venv = store / "venv"
    exe = venv_python(venv)
    stamp = venv / ".requirements.sha256"
    want = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest() if REQUIREMENTS.exists() else ""
    if exe.exists() and stamp.exists() and stamp.read_text().strip() == want and python_ok(str(exe)):
        say(f"Python ready: {run([str(exe), '--version']).stdout.strip()} (venv under {store.name})")
        return str(exe)
    if not install:
        say("Python packages missing: run setup again without --no-install")
        return None
    major, minor = sys.version_info[:2]
    if not (3, 10) <= (major, minor) <= (3, 15):
        say(f"Python {major}.{minor} found; the packages need 3.10 to 3.15. Install one from https://www.python.org/downloads/ and run setup again.")
        return None
    if not exe.exists():
        say("Creating a Python environment for the pipeline…")
        r = run([sys.executable, "-m", "venv", str(venv)])
        if r.returncode != 0:
            say("Could not create the Python environment: " + (r.stderr.strip().splitlines() or ["unknown"])[-1][:160])
            return None
    say("Installing Python packages (a few minutes the first time)…")
    # wheels only: a Python without prebuilt numpy or scipy fails here in seconds with the package named,
    # instead of pip compiling scipy from source for half an hour and failing for want of a Fortran compiler
    r = run([str(exe), "-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:", "-q", "-r", str(REQUIREMENTS)])
    if r.returncode != 0 or not python_ok(str(exe)):
        say("Python packages did not install: " + (r.stderr.strip().splitlines() or ["unknown"])[-1][:160])
        return None
    stamp.write_text(want)
    say(f"Python ready: {run([str(exe), '--version']).stdout.strip()} (venv under {store.name})")
    return str(exe)


# --------------------------------------------------------------------------- tools

def first_existing(cands) -> str | None:
    for c in cands:
        if not c:
            continue
        for hit in (glob.glob(c) if any(ch in c for ch in "*?[") else [c]):
            if Path(hit).exists():
                return hit
    return None


def find_chrome() -> str | None:
    local = os.environ.get("LOCALAPPDATA", "")
    return first_existing([
        os.environ.get("CHROME"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"), shutil.which("google-chrome-stable"), shutil.which("chromium"), shutil.which("chromium-browser"),
        "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        f"{local}/Google/Chrome/Application/chrome.exe" if local else None,
    ])


def find_ffmpeg() -> str | None:
    local = os.environ.get("LOCALAPPDATA", "")
    return first_existing([shutil.which("ffmpeg"), f"{local}/Microsoft/WinGet/Packages/Gyan.FFmpeg*/**/bin/ffmpeg.exe" if local else None])


def installer(tool: str) -> list[str] | None:
    """The command that installs `tool` here, or None when there is no package manager to do it."""
    if OS == "Windows" and shutil.which("winget"):
        ids = {"chrome": "Google.Chrome", "ffmpeg": "Gyan.FFmpeg"}
        return ["winget", "install", "--id", ids[tool], "-e", "--silent", "--accept-package-agreements", "--accept-source-agreements"]
    if OS == "Darwin" and shutil.which("brew"):
        return {"chrome": ["brew", "install", "--cask", "google-chrome"], "ffmpeg": ["brew", "install", "ffmpeg"]}[tool]
    if OS == "Linux":
        pkgs = {"chrome": "chromium", "ffmpeg": "ffmpeg"}
        if shutil.which("apt-get"):
            return ["sudo", "-n", "apt-get", "install", "-y", pkgs[tool] if tool != "chrome" else "chromium-browser"]
        if shutil.which("dnf"):
            return ["sudo", "-n", "dnf", "install", "-y", pkgs[tool]]
    return None


def setup_tool(name: str, find, url: str, install: bool) -> str | None:
    label = {"chrome": "Chrome", "ffmpeg": "ffmpeg"}[name]
    path = find()
    if path:
        say(f"{label} found")
        return path
    cmd = installer(name)
    if not install or not cmd:
        say(f"{label} missing: install it from {url}" + (f", or run: {' '.join(cmd)}" if cmd else ""))
        return None
    say(f"{label} missing; installing (this may ask for your permission on screen)…")
    r = run(cmd)
    path = find()
    if path:
        say(f"{label} installed")
        return path
    why = (r.stderr.strip() or r.stdout.strip()).splitlines()[-1:] or ["no details"]
    say(f"{label} not installed ({why[0][:120]}). Install it from {url}, or run: {' '.join(cmd)}")
    return None


# --------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-install", action="store_true", help="only report; install nothing")
    a = ap.parse_args()
    install = not a.no_install
    store = store_dir()
    env_file = store / ".env"
    saved = integrate.read_env(env_file)
    for k in ("DAPILOT_PYTHON", "CHROME"):   # what an earlier run wrote counts as set
        if saved.get(k) and not os.environ.get(k):
            os.environ[k] = saved[k]

    updates: dict[str, str] = {}
    py = setup_python(store, install)
    if py and saved.get("DAPILOT_PYTHON") != py:
        updates["DAPILOT_PYTHON"] = py
    chrome = setup_tool("chrome", find_chrome, CHROME_URL, install)
    if chrome and saved.get("CHROME") != chrome:
        updates["CHROME"] = chrome
    ffmpeg = setup_tool("ffmpeg", find_ffmpeg, FFMPEG_URL, install)
    if ffmpeg and not shutil.which("ffmpeg"):
        say("ffmpeg is installed but not on this session's PATH yet; a new terminal will have it.")
    if updates:
        integrate.write_env(env_file, updates)
    return 0 if all((py, chrome, ffmpeg)) else 1


if __name__ == "__main__":
    sys.exit(main())
