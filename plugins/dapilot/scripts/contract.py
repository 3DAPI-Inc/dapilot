#!/usr/bin/env python3
"""The contract between the plugin and whatever reads what it writes (2026-10-25).

Three documents cross the plugin's boundary and used to be re-derived by every
reader with its own rules: the director's events, the ledger's markers, and the
outcome of a run. Each now has one JSON Schema under plugins/dapilot/schema/,
every instance carries `schema_version`, and this module is the one place the
plugin validates against them. The hosted backend (3dapi) holds a vendored copy
of the same files and validates what the runner posts; a reader accepts any
minor version of the major it holds.

    contract.py check <event|marker|outcome> [file|-]   exit 0, or 1 with the problems
    contract.py selftest                                every example validates; a broken one does not
    contract.py version

In code: `check(doc, name)` stamps the version and raises ContractError on a
problem; `problems(doc, name)` lists them; `accepts(version)` says whether a
document's version is one this plugin reads.
"""
from __future__ import annotations

import functools
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = PLUGIN_ROOT / "schema"
VERSION = (SCHEMA_DIR / "VERSION").read_text().strip()
NAMES = ("event", "marker", "outcome")


class ContractError(ValueError):
    """A document the plugin was about to write or send breaks its own contract. A bug, not a condition."""


@functools.lru_cache(maxsize=None)
def schema(name: str) -> dict:
    if name not in NAMES:
        raise KeyError(f"no schema named {name!r}; one of {', '.join(NAMES)}")
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text())


@functools.lru_cache(maxsize=None)
def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(schema(name), format_checker=Draft202012Validator.FORMAT_CHECKER)


def problems(doc: object, name: str) -> list[str]:
    """Every way `doc` breaks the schema, as `path: message`, worst first. Empty when it conforms."""
    found = []
    for err in sorted(validator(name).iter_errors(doc), key=lambda e: (len(e.absolute_path), str(e.absolute_path))):
        where = "/".join(str(p) for p in err.absolute_path) or "$"
        found.append(f"{where}: {err.message}")
    return found


def stamp(doc: dict) -> dict:
    doc.setdefault("schema_version", VERSION)
    return doc


def check(doc: dict, name: str) -> dict:
    """The document, stamped with this plugin's version, or ContractError naming what is wrong."""
    stamp(doc)
    bad = problems(doc, name)
    if bad:
        raise ContractError(f"{name} document breaks the contract: " + "; ".join(bad[:5]))
    return doc


def accepts(version: object) -> bool:
    """A reader holds one major and takes any minor of it: additions are minors, anything else is a major."""
    return isinstance(version, str) and version.split(".")[0] == VERSION.split(".")[0]


def selftest() -> int:
    """Every example under schema/examples/ validates against the schema its name starts with, and a
    document with a field removed does not. Run by doctor.sh and test_phase0.sh."""
    examples = sorted((SCHEMA_DIR / "examples").glob("*.json"))
    if not examples:
        print("FAIL  no examples under schema/examples/")
        return 1
    bad = 0
    for p in examples:
        name = p.name.split(".")[0]
        doc = json.loads(p.read_text())
        found = problems(doc, name)
        if found:
            bad += 1
            print(f"FAIL  {p.name}: " + "; ".join(found[:3]))
            continue
        broken = {k: v for k, v in doc.items() if k != "schema_version"}
        if not problems(broken, name):
            bad += 1
            print(f"FAIL  {p.name}: still validates without schema_version")
            continue
        print(f"ok    {p.name} ({name} {doc.get('schema_version')})")
    return 1 if bad else 0


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] not in ("check", "selftest", "version"):
        print(__doc__, file=sys.stderr)
        return 2
    if args[0] == "version":
        print(VERSION)
        return 0
    if args[0] == "selftest":
        return selftest()
    if len(args) < 2 or args[1] not in NAMES:
        print(f"usage: contract.py check <{'|'.join(NAMES)}> [file|-]", file=sys.stderr)
        return 2
    raw = sys.stdin.read() if len(args) < 3 or args[2] == "-" else Path(args[2]).read_text()
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "problems": [f"not JSON: {e}"]}))
        return 1
    found = problems(doc, args[1])
    print(json.dumps({"ok": not found, "schema": args[1], "version": VERSION, "problems": found}))
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
