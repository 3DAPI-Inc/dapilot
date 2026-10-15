#!/usr/bin/env python3
"""UserPromptSubmit hook: a model file in the person's message starts the listing, every time.

Reads the hook's JSON on stdin, looks for a path or attachment ending in .stl, .3mf, .obj, .step or
.stp, and when it finds one prints a short context block that tells the model to run the
dapilot:quickstart command with exactly that path, now, instead of talking about the file. A dropped
file is otherwise a judgement call for the model, and on 2026-10-14 the judgement went the other way:
the person dropped a .3mf and got a conversation. This makes it a rule.

Says nothing when the message holds no model file, or already is a /dapilot command. Never blocks the
prompt (exit 0 always). Standard library only; must finish well inside the 30 s hook budget.
"""
from __future__ import annotations

import json
import re
import sys

EXTS = r"(?:stl|3mf|obj|step|stp)"
# a quoted path, or a run of non-space characters, ending in a model extension; case-insensitive
# quoted first (a path with spaces), then a bare run of non-space characters; the boundary belongs to the bare form only
PATH = re.compile(r'"([^"\n]+\.' + EXTS + r')"|\'([^\'\n]+\.' + EXTS + r')\'|(\S+\.' + EXTS + r')\b', re.IGNORECASE)


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    text = ""
    if isinstance(data, dict):
        for key in ("prompt", "user_prompt", "message", "input"):
            v = data.get(key)
            if isinstance(v, str) and v:
                text = v
                break
    if not text:
        text = raw
    if not text or text.lstrip().startswith("/dapilot:"):
        return 0
    found = []
    for m in PATH.finditer(text):
        p = next(g for g in m.groups() if g)
        p = p.strip("`<>()[],;").lstrip("@")   # an @-mention is a path with a prefix
        if p and p not in found:
            found.append(p)
    if not found:
        return 0
    paths = " ".join(f'"{p}"' if " " in p else p for p in found[:5])
    plural = len(found) > 1
    context = (
        f"dapilot: the message contains {'model files' if plural else 'a 3D model file'}: {paths}. "
        "Do not describe, open, read or analyse the file and do not ask what to do with it. "
        f"Invoke the dapilot:quickstart command now with {'these paths' if plural else 'this path'} as its argument, "
        "exactly as given, and follow that command's procedure to the end: it sets the machine up, checks the accounts, "
        "starts the run, reports each step, and asks for the sale price when it is ready. "
        "If the person's words ask for something else about the file as well, answer that after the run has started."
    )
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
