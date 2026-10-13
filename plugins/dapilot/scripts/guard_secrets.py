#!/usr/bin/env python3
"""PreToolUse hook: the agent's tools may not read, edit or dump the files that hold credentials.

Protected, under the store dir ($DAPILOT_STORE_DIR, else <project>/store):
    .env and .env.*            (not .env.example)
    etsy_token*.json
    social/sessions/**  social/profiles/**
    integrations.etsy-login.log

Read, Edit, Write, Grep, Glob and NotebookEdit are refused when their path is one
of those or a directory that contains them. Bash is refused when the command
names one of them, except as the argument of `--env`, which is how the plugin's
own scripts take the file without the model seeing it. The scripts themselves
still open the files: this hook fences the model's tools, not code.

Registered in hooks/hooks.json. Exit 2 refuses, with the reason on stderr.
Anything unexpected exits 0: a broken guard must not stop the pipeline.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Grep", "Glob"}
PATH_KEYS = ("file_path", "path", "notebook_path")
# Text patterns for Bash. Relative spellings are caught as text because the model types them that way.
TEXT = [
    r"(?<![\w.-])store/\.env(?!\.example)",
    r"\$\{?DAPILOT_STORE_DIR\}?/\.env(?!\.example)",
    r"etsy_token[\w.-]*\.json",
    r"social/(sessions|profiles)(/|\b)",
    r"integrations\.etsy-login\.log",
]


def store_dir(cwd: str) -> Path:
    s = os.environ.get("DAPILOT_STORE_DIR")
    if s:
        return Path(s).expanduser().resolve()
    root = os.environ.get("DAPILOT_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or cwd
    return (Path(root) / "store").resolve()


def protected(p: Path, store: Path) -> bool:
    try:
        rel = p.resolve().relative_to(store)
    except (ValueError, OSError):
        return False
    parts = rel.parts
    if not parts:  # the store dir itself: a Grep or Glob over it would read .env
        return True
    name = parts[0]
    if name.startswith(".env") and name != ".env.example":
        return True
    if name.startswith("etsy_token") and name.endswith(".json"):
        return True
    if name == "social" and (len(parts) == 1 or parts[1] in ("sessions", "profiles")):
        return True
    return name == "integrations.etsy-login.log"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    tool = payload.get("tool_name", "")
    inp = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    store = store_dir(cwd)

    if tool in FILE_TOOLS:
        for k in PATH_KEYS:
            v = inp.get(k)
            if isinstance(v, str) and v:
                p = Path(v) if os.path.isabs(v) else Path(cwd) / v
                if protected(p, store):
                    print(f"{tool} on {v} refused: it holds credentials. Scripts use the file through --env; "
                          f"people edit it on the Integrations page (/dapilot:integrate).", file=sys.stderr)
                    return 2
        if tool in ("Grep", "Glob") and not inp.get("path") and protected(Path(cwd), store):
            print(f"{tool} over the store dir refused: it holds credentials.", file=sys.stderr)
            return 2
        return 0

    if tool == "Bash":
        cmd = inp.get("command") or ""
        # the one allowed spelling: `--env <file>` or `--env=<file>` as a script argument
        stripped = re.sub(r"--env(?:=|\s+)(\"[^\"]*\"|'[^']*'|\S+)", "--env <file>", cmd)
        abs_env = str(store / ".env")
        if abs_env in stripped or any(re.search(t, stripped) for t in TEXT):
            print("Bash refused: the command names a credential file (store/.env, an Etsy token, a social session). "
                  "Pass it only as `--env <file>` to a plugin script; never read, print, copy or edit it. "
                  "People change values on the Integrations page (/dapilot:integrate).", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001  a guard that crashes must not block the run
        sys.exit(0)
