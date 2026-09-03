#!/usr/bin/env python3
"""The run ledger: the only thing that knows what has been done.

Deterministic bookkeeping for one run. Leads call it in a loop; the model
never edits state.json by hand.

  ledger.py init   --run ID --folder DIR [--department D]   hash files into work/, create the run
  ledger.py next   --run ID [--max N]                        briefs for stages ready to dispatch
  ledger.py record --run ID --result FILE|- [--tokens N --model M]
  ledger.py status --run ID
  ledger.py report --run ID                                   write runs/ID/report.md

Layout: <root>/work/<sha256>/ per item, <root>/runs/<ID>/state.json per run.
<root> is DAPILOT_PROJECT_DIR, else CLAUDE_PROJECT_DIR, else cwd. Config is
<DAPILOT_STORE_DIR or root/store>/config.json, falling back to config.example.json.
The stage table is --stages FILE, default the intake table shipped in the plugin.

Rules enforced here, not in prompts: a stage is ready only when every input
exists and no output exists; a stage returned by `next` is marked running and
counts as an attempt; a running stage older than max_wall_minutes is reset
once; a result for a stage that is not running is rejected; an ok result whose
outputs are missing on disk becomes a failure; attempts beyond max_attempts
become failed; needs_human becomes blocked and is never re-dispatched.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_result import validate  # noqa: E402

SUPPORTED = {".stl", ".3mf", ".obj", ".step", ".stp"}
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STAGES = PLUGIN_ROOT / "skills" / "run-intake" / "reference" / "stages.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def root() -> Path:
    return Path(os.environ.get("DAPILOT_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()


def load_config() -> dict:
    store = Path(os.environ.get("DAPILOT_STORE_DIR") or root() / "store")
    for name in ("config.json", "config.example.json"):
        p = store / name
        if p.exists():
            cfg = json.loads(p.read_text())
            cfg["_source"] = str(p)
            return cfg
    raise SystemExit(out({"ok": False, "reason": f"no config.json or config.example.json in {store}"}, 2))


def out(obj, code: int = 0) -> int:
    print(json.dumps(obj, indent=2))
    return code


def run_dir(run: str) -> Path:
    return root() / "runs" / run


def load_state(run: str) -> dict:
    p = run_dir(run) / "state.json"
    if not p.exists():
        raise SystemExit(out({"ok": False, "reason": f"no such run: {run}"}, 1))
    return json.loads(p.read_text())


def save_state(state: dict) -> None:
    p = run_dir(state["run"]) / "state.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(p)


def load_stages(path: str | None) -> dict:
    return json.loads(Path(path or DEFAULT_STAGES).read_text())


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def exists(workdir: Path, pattern: str) -> bool:
    return bool(glob.glob(str(workdir / pattern)))


# ------------------------------------------------------------------ init

def cmd_init(a) -> int:
    cfg = load_config()
    stages = load_stages(a.stages)
    folder = Path(a.folder)
    if not folder.is_dir():
        return out({"ok": False, "reason": f"not a directory: {folder}"}, 1)
    rd = run_dir(a.run)
    rd.mkdir(parents=True, exist_ok=True)
    state_path = rd / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        "run": a.run, "process": stages["process"], "department": a.department, "started": now(),
        "stages_file": str(Path(a.stages or DEFAULT_STAGES).resolve()),
        "budget": {"max_attempts": cfg.get("max_attempts", 2), "max_wall_minutes": cfg.get("max_wall_minutes", 60),
                   "max_workers": cfg.get("max_workers_per_lead", 3), "usd_per_run": cfg.get("budget", {}).get("usd_per_run")},
        "items": {}, "cost": {"tokens": 0, "usd": 0.0, "by_agent": {}}, "notes": [],
    }
    added = []
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() not in SUPPORTED or not f.is_file():
            continue
        h = sha256(f)
        wd = root() / "work" / h
        wd.mkdir(parents=True, exist_ok=True)
        src = wd / f"source{f.suffix.lower()}"
        if not src.exists():
            shutil.copy2(f, src)
        meta = wd / "meta.json"
        if not meta.exists():
            meta.write_text(json.dumps({"file": f.name, "bytes": f.stat().st_size, "sha256": h, "first_seen": now()}, indent=2))
        if h not in state["items"]:
            state["items"][h] = {"file": f.name, "workdir": str(wd.relative_to(root())), "stages": {}}
            added.append(h)
    save_state(state)
    return out({"ok": True, "run": a.run, "state": str(state_path), "items": len(state["items"]), "added": added})


# ------------------------------------------------------------------ next

def refresh(state: dict, stages: dict) -> None:
    """Mark stages done from what exists on disk; reset stale running stages."""
    max_min = state["budget"]["max_wall_minutes"]
    for h, item in state["items"].items():
        wd = root() / item["workdir"]
        for st in stages["stages"]:
            if not st.get("enabled", True):
                continue  # disabled stages are vocabulary, not work
            rec = item["stages"].setdefault(st["stage"], {"status": "queued", "attempt": 0})
            if all(exists(wd, p) for p in st["produces"]) and rec["status"] not in ("done",):
                rec.update({"status": "done", "finished": rec.get("finished") or now()})
                if "summary" not in rec:
                    rec["summary"] = "output present on disk"
            elif rec["status"] == "running":
                started = datetime.fromisoformat(rec["dispatched"])
                age = (datetime.now(timezone.utc) - started).total_seconds() / 60
                if age > max_min:
                    rec["status"] = "queued" if rec["attempt"] < state["budget"]["max_attempts"] else "failed"
                    rec["previous"] = f"attempt {rec['attempt']} exceeded {max_min} minutes with no result"
                    state["notes"].append(f"{now()} {h[:8]} {st['stage']}: stale running stage reset")


def ready_briefs(state: dict, stages: dict) -> list[dict]:
    briefs = []
    for h, item in state["items"].items():
        wd = root() / item["workdir"]
        if (wd / ".lock").exists():
            continue
        for st in stages["stages"]:
            if not st.get("enabled", True):
                continue
            rec = item["stages"][st["stage"]]
            if rec["status"] in ("running", "blocked", "failed"):
                break  # this item is occupied or stuck; later stages wait
            if rec["status"] != "queued":
                continue
            if not all(exists(wd, r) for r in st["requires"]):
                break  # inputs missing means an earlier stage is not done
            if rec["attempt"] >= state["budget"]["max_attempts"]:
                rec["status"] = "failed"
                rec["reason"] = rec.get("previous") or "attempts exhausted"
                continue
            briefs.append({
                "run": state["run"], "department": state["department"], "item": h,
                "workdir": item["workdir"], "file": item["file"], "stage": st["stage"], "skill": st["skill"],
                "requires": st["requires"], "produces": st["produces"], "gate": st["gate"],
                "attempt": rec["attempt"] + 1, "previous": rec.get("previous"),
            })
            break  # one stage per item per pass; stages of one item are sequential
    return briefs


def cmd_next(a) -> int:
    state = load_state(a.run)
    stages = load_stages(state.get("stages_file"))
    refresh(state, stages)
    limit = a.max or state["budget"]["max_workers"]
    briefs = ready_briefs(state, stages)[:limit]
    for b in briefs:
        rec = state["items"][b["item"]]["stages"][b["stage"]]
        rec.update({"status": "running", "attempt": b["attempt"], "dispatched": now()})
    save_state(state)
    return out({"ok": True, "briefs": briefs, "remaining": remaining(state)})


def remaining(state: dict) -> dict:
    counts = {"queued": 0, "running": 0, "blocked": 0, "done": 0, "failed": 0}
    for item in state["items"].values():
        for rec in item["stages"].values():
            counts[rec["status"]] = counts.get(rec["status"], 0) + 1
    return counts


# ---------------------------------------------------------------- record

def cmd_record(a) -> int:
    state = load_state(a.run)
    raw = sys.stdin.read() if a.result == "-" else Path(a.result).read_text()
    try:
        res = json.loads(raw)
    except json.JSONDecodeError as e:
        return out({"ok": False, "reason": f"result is not JSON: {e}"}, 1)
    errors = validate(res, expect={"run": a.run})
    if errors:
        state["notes"].append(f"{now()} rejected malformed result: {errors[:3]}")
        save_state(state)
        return out({"ok": False, "reason": "malformed result", "errors": errors}, 1)
    item = state["items"].get(res["item"])
    if not item:
        return out({"ok": False, "reason": f"unknown item {res['item']}"}, 1)
    rec = item["stages"].get(res["stage"])
    if not rec or rec["status"] != "running":
        state["notes"].append(f"{now()} rejected result for {res['item'][:8]} {res['stage']}: stage not running")
        save_state(state)
        return out({"ok": False, "reason": f"stage {res['stage']} is not running for this item; not dispatched or already recorded"}, 1)

    wd = root() / item["workdir"]
    rec["summary"] = res["summary"]
    rec["agent"] = res["agent"]
    rec["finished"] = now()
    if res["needs_human"]:
        rec["status"] = "blocked"
        rec["question"] = res["needs_human"]
    elif res["ok"]:
        stages = load_stages(state.get("stages_file"))
        st = next(s for s in stages["stages"] if s["stage"] == res["stage"])
        missing = [p for p in st["produces"] if not exists(wd, p)]
        if missing:
            rec["status"] = "queued" if rec["attempt"] < state["budget"]["max_attempts"] else "failed"
            rec["previous"] = f"claimed ok but outputs missing: {missing}"
            state["notes"].append(f"{now()} {res['item'][:8]} {res['stage']}: ok claimed, outputs missing {missing}")
        else:
            rec["status"] = "done"
            rec["cached"] = res["cached"]
    else:
        rec["previous"] = res["reason"]
        rec["status"] = "queued" if rec["attempt"] < state["budget"]["max_attempts"] else "failed"
        if rec["status"] == "failed":
            rec["reason"] = res["reason"]
    if a.tokens or a.usd is not None:
        cfg = load_config()
        price = cfg.get("pricing_usd_per_1k_tokens", {}).get(a.model or "inherit", 0.0)
        usd = a.usd if a.usd is not None else (a.tokens or 0) / 1000 * price
        c = state["cost"]
        c["tokens"] += a.tokens or 0
        c["usd"] = round(c["usd"] + usd, 4)
        by = c["by_agent"].setdefault(res["agent"], {"tokens": 0, "usd": 0.0, "results": 0})
        by["tokens"] += a.tokens
        by["usd"] = round(by["usd"] + usd, 4)
        by["results"] += 1
        rec["tokens"] = a.tokens or 0
        rec["usd"] = round(usd, 4)
    save_state(state)
    return out({"ok": True, "item": res["item"], "stage": res["stage"], "status": rec["status"], "remaining": remaining(state)})


# ---------------------------------------------------------------- status

def cmd_status(a) -> int:
    state = load_state(a.run)
    stages = load_stages(state.get("stages_file"))
    refresh(state, stages)
    save_state(state)
    ready = len(ready_briefs(state, stages))
    save_state(state)
    r = remaining(state)
    over = state["budget"].get("usd_per_run") is not None and state["cost"]["usd"] > state["budget"]["usd_per_run"]
    return out({"ok": True, "run": a.run, "counts": r, "ready": ready, "cost": state["cost"],
                "over_budget": over, "work_left": (ready + r["running"]) > 0 and not over})


# ---------------------------------------------------------------- report

def cmd_report(a) -> int:
    state = load_state(a.run)
    stages = load_stages(state.get("stages_file"))
    refresh(state, stages)
    lines = [f"# Run {state['run']}", "", f"Process: {state['process']}  Department: {state['department']}  Started: {state['started']}", ""]
    lines += ["| item | file | " + " | ".join(s["stage"] for s in stages["stages"] if s.get("enabled", True)) + " |",
              "|---|---|" + "---|" * len([s for s in stages["stages"] if s.get("enabled", True)])]
    blocked, failed = [], []
    for h, item in state["items"].items():
        cells = []
        for st in stages["stages"]:
            if not st.get("enabled", True):
                continue
            rec = item["stages"].get(st["stage"], {"status": "queued"})
            cells.append(rec["status"] + (" (cached)" if rec.get("cached") else ""))
            if rec["status"] == "blocked":
                blocked.append((h, item["file"], st["stage"], rec.get("question", "")))
            if rec["status"] == "failed":
                failed.append((h, item["file"], st["stage"], rec.get("reason", rec.get("previous", ""))))
        lines.append(f"| {h[:8]} | {item['file']} | " + " | ".join(cells) + " |")
    if blocked:
        lines += ["", "## Waiting for a person", ""]
        lines += [f"- {h[:8]} {f} at {s}: {q}" for h, f, s, q in blocked]
    if failed:
        lines += ["", "## Failed", ""]
        lines += [f"- {h[:8]} {f} at {s}: {r}" for h, f, s, r in failed]
    if state["notes"]:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in state["notes"]]
    c = state["cost"]
    r = remaining(state)
    per_item = round(c["usd"] / max(1, r["done"]), 4)
    lines += ["", "## Cost", "", f"- stages done: {r['done']}, failed: {r['failed']}, blocked: {r['blocked']}",
              f"- tokens: {c['tokens']}, estimated USD: {c['usd']}, per done stage: {per_item}",
              f"- by agent: {json.dumps(c['by_agent'])}"]
    (run_dir(state["run"]) / "report.md").write_text("\n".join(lines) + "\n")
    save_state(state)
    return out({"ok": True, "report": str(run_dir(state["run"]) / "report.md"), "counts": r, "cost": c})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("--run", required=True); s.add_argument("--folder", required=True)
    s.add_argument("--department", default="catalogue"); s.add_argument("--stages"); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("next"); s.add_argument("--run", required=True); s.add_argument("--max", type=int); s.set_defaults(fn=cmd_next)
    s = sub.add_parser("record"); s.add_argument("--run", required=True); s.add_argument("--result", required=True)
    s.add_argument("--tokens", type=int); s.add_argument("--usd", type=float, help="exact cost if the runtime reports it; overrides the token price estimate")
    s.add_argument("--model"); s.set_defaults(fn=cmd_record)
    s = sub.add_parser("status"); s.add_argument("--run", required=True); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("report"); s.add_argument("--run", required=True); s.set_defaults(fn=cmd_report)
    a = ap.parse_args()
    try:
        return a.fn(a)
    except SystemExit as e:
        return int(e.code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
