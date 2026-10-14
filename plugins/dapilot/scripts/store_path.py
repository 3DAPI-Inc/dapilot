#!/usr/bin/env python3
"""Where this machine keeps its store: the env file with the keys, the Etsy token, the config,
the pricing policy, the products, the social sessions. One answer per machine, so that a new
session, a worktree, an update or a reinstall never starts a seller over.

    store_path.py            prints the path (and creates the directory)

The rule, first match wins:
  1. $DAPILOT_STORE_DIR, when set. The dev scripts and the job server set it for everything they start.
  2. <project>/store, when that directory already holds a .env or a config.json: a checkout of the
     repo that was set up in place. <project> is $DAPILOT_PROJECT_DIR, else $CLAUDE_PROJECT_DIR,
     else the working directory.
  3. ~/.dapilot, created on first use. The default for an installed plugin, and for a session whose
     working directory is a worktree or a scratch folder with no store of its own.

Every script that needs the store imports `store_dir` from here, or runs this file and reads the
line it prints, so they cannot disagree.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HOME_STORE = Path.home() / ".dapilot"


def project_dir() -> Path:
    return Path(os.environ.get("DAPILOT_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()


def store_dir(create: bool = True) -> Path:
    s = os.environ.get("DAPILOT_STORE_DIR")
    if s:
        p = Path(s).expanduser()
        p = p if p.is_absolute() else project_dir() / p
    else:
        local = project_dir() / "store"
        p = local if (local / ".env").exists() or (local / "config.json").exists() else HOME_STORE
    p = p.resolve()
    if create:
        p.mkdir(parents=True, exist_ok=True)
        if p == HOME_STORE.resolve():
            p.chmod(0o700)
    return p


if __name__ == "__main__":
    print(store_dir())
    sys.exit(0)
