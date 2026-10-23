#!/usr/bin/env python3
"""Check a worker or lead result line against the output contract.

Every agent in dapilot ends by printing one JSON object. The level above
runs this before accepting it. Usage:

  validate_result.py [--expect key=value ...] [--workdir DIR] < result.json

Exit 0 and {"ok": true} when the result is well formed; exit 1 with the list
of problems otherwise. --expect checks fields against what the dispatcher
sent in the brief (item, stage, department, run). --workdir checks that every
listed output exists there.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED = {
    "ok": bool, "cached": bool, "run": str, "item": str, "stage": str,
    "agent": str, "department": str, "outputs": list, "summary": str,
}
NULLABLE_STR = ("needs_human", "reason")
# Optional: a script's own code for a failure (`error`, the supervisor's tier one, 2026-10-23). Absent is fine.
OPTIONAL_STR = ("error",)
SUMMARY_MAX = 200


def validate(obj, expect: dict | None = None, workdir: str | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ["result is not a JSON object"]
    for key, typ in REQUIRED.items():
        if key not in obj:
            errors.append(f"missing field: {key}")
        elif not isinstance(obj[key], typ):
            errors.append(f"{key}: expected {typ.__name__}, got {type(obj[key]).__name__}")
    for key in NULLABLE_STR:
        if key not in obj:
            errors.append(f"missing field: {key}")
        elif obj[key] is not None and not isinstance(obj[key], str):
            errors.append(f"{key}: expected string or null")
    for key in OPTIONAL_STR:
        if key in obj and obj[key] is not None and not isinstance(obj[key], str):
            errors.append(f"{key}: expected string or null")
    extra = set(obj) - set(REQUIRED) - set(NULLABLE_STR) - set(OPTIONAL_STR)
    if extra:
        errors.append(f"unknown fields: {sorted(extra)}")
    if errors:
        return errors

    if obj["ok"] is False and not obj["reason"]:
        errors.append("ok is false but reason is empty")
    if obj["ok"] is True and obj["reason"]:
        errors.append("ok is true but reason is set; use summary")
    if obj["cached"] and obj["ok"] is False:
        errors.append("cached results cannot be failures")
    s = obj["summary"]
    if not s.strip():
        errors.append("summary is empty")
    if "\n" in s:
        errors.append("summary must be one line")
    if len(s) > SUMMARY_MAX:
        errors.append(f"summary longer than {SUMMARY_MAX} characters")
    for o in obj["outputs"]:
        if not isinstance(o, str) or not o:
            errors.append(f"outputs: bad entry {o!r}")
            continue
        if o.startswith("/") or ".." in Path(o).parts:
            errors.append(f"outputs: {o} must be a relative path inside the work dir")
        elif workdir and not (Path(workdir) / o).exists():
            errors.append(f"outputs: {o} does not exist in {workdir}")
    if obj["ok"] and not obj["cached"] and not obj["outputs"] and not obj["needs_human"]:
        errors.append("ok without outputs, cache hit, or needs_human: nothing happened")
    for key, val in (expect or {}).items():
        if str(obj.get(key)) != str(val):
            errors.append(f"{key}: expected {val!r}, got {obj.get(key)!r}")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--expect", action="append", default=[])
    ap.add_argument("--workdir")
    a = ap.parse_args()
    expect = dict(e.split("=", 1) for e in a.expect)
    raw = sys.stdin.read()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "errors": [f"not JSON: {e}"]}))
        return 1
    errors = validate(obj, expect, a.workdir)
    print(json.dumps({"ok": not errors, "errors": errors}))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
