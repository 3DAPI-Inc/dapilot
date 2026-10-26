#!/usr/bin/env python3
"""The run ledger: the only thing that knows what has been done.

Deterministic bookkeeping for one run. Leads call it in a loop; the model
never edits state.json by hand.

  ledger.py init   --run ID --folder DIR [--department D]   hash files into work/, create the run
  ledger.py next   --run ID [--max N]                        briefs for stages ready to dispatch
  ledger.py record --run ID --result FILE|- [--tokens N --model M]
  ledger.py status --run ID
  ledger.py report --run ID                                   write runs/ID/report.md
  ledger.py retry  --run ID --item HASH --stage S [--reason T --by NAME]
                                                              take a stopped stage up again

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

A stage that ends failed or blocked leaves a marker in the work dir,
<stage>.failed.json or <stage>.blocked.json (2026-09-23), so the console, a
resume and the next run all read the same fact: until a person clears the
marker (the console's Try again, or rm), no run dispatches that stage again.
Before the marker, failure lived only in one run's state.json and every
resume re-bought it. A marker is cleared by the ledger itself when the
stage's outputs turn up on disk.

Taking a stopped stage up again is `retry` (2026-10-22): it clears the
stage's marker and queues the stage in the named run with fresh attempts,
keeping what it ended with as `previous` and counting the retry on the
record, so a retry is a ledger fact with a reason and not an rm. The run's
own state used to outlive the marker: a stage the run had recorded as
failed or blocked stayed so after the marker was removed, which made a
retry under the same run id a no-op (seen on the hosted runner 2026-10-21).
Now a failed or blocked record whose marker is gone is queued again on the
next refresh, so rm still works as the marker says it does.

The ledger is the only reader of the work dirs (2026-10-25). `outcome` emits
the one document everything else reads: per item per stage, the open gates,
every marker and the media an item has so far, validated against
schema/outcome.schema.json. The hosted runner posts it, the dashboard renders
it, the supervisor reads its markers; none of them opens a work dir. Markers
are validated against schema/marker.schema.json as they are written.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store_path import store_dir as _store_dir  # noqa: E402  one rule for every script

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_result import validate  # noqa: E402
from source_thumbnails import extract as extract_thumbnails  # noqa: E402
from check_outputs import check as check_outputs  # noqa: E402  hard and soft problems in a stage's files
from contract import check as contract_check  # noqa: E402  the marker and outcome schemas (schema/)

SUPPORTED = {".stl", ".3mf", ".obj", ".step", ".stp"}
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STAGES = PLUGIN_ROOT / "skills" / "run-intake" / "reference" / "stages.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def root() -> Path:
    return Path(os.environ.get("DAPILOT_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()


def load_config() -> dict:
    store = _store_dir()
    for p in (store / "config.json", Path(__file__).resolve().parent.parent / "templates" / "config.example.json"):
        if p.exists():
            cfg = json.loads(p.read_text())
            cfg["_source"] = str(p)
            return cfg
    raise SystemExit(out({"ok": False, "reason": f"no config.json in {store} and no template in the plugin"}, 2))


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
    # The state records the stages file by absolute path; a root that moves between machines (a hosted
    # store read on a laptop, 2026-10-26) falls back to the plugin's own table rather than failing.
    chosen = Path(path) if path and Path(path).is_file() else DEFAULT_STAGES
    return json.loads(chosen.read_text())


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def exists(workdir: Path, pattern: str) -> bool:
    return bool(glob.glob(str(workdir / pattern)))


# ------------------------------------------------------------------ init

def worker_minutes(stage: str | None, cfg: dict | None = None) -> float:
    """Wall clock a worker may hold an item: `minutes_per_stage` for its stage, else `max_worker_minutes`."""
    cfg = cfg if cfg is not None else load_config()
    base = float(cfg.get("max_worker_minutes", 15))
    return float((cfg.get("minutes_per_stage") or {}).get(stage, base)) if stage else base


def lock_stage(wd: Path) -> str | None:
    """The stage a .lock names. Workers write JSON or plain text; read either."""
    try:
        text = (wd / ".lock").read_text()
    except OSError:
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and isinstance(obj.get("stage"), str):
            return obj["stage"]
    except json.JSONDecodeError:
        pass
    for token in text.replace(",", " ").replace(":", " ").split():
        if token in ("analyze", "quote", "preview", "research", "price", "design", "render", "copy", "publish"):
            return token
    return None


def lock_is_stale(wd: Path, max_minutes: float | None = None) -> bool:
    """A worker that died mid-stage leaves its .lock behind; past its stage's budget it means nothing."""
    lock = wd / ".lock"
    if not lock.exists():
        return False
    if max_minutes is None:
        max_minutes = worker_minutes(lock_stage(wd))
    age_min = (datetime.now(timezone.utc).timestamp() - lock.stat().st_mtime) / 60
    return age_min > max_minutes


def clear_stale_lock(wd: Path, max_minutes: float | None = None, notes: list | None = None) -> bool:
    if max_minutes is None:
        max_minutes = worker_minutes(lock_stage(wd))
    if lock_is_stale(wd, max_minutes):
        (wd / ".lock").unlink(missing_ok=True)
        if notes is not None:
            notes.append(f"{now()} {wd.name[:8]}: removed a .lock older than {max_minutes:g} minutes")
        return True
    return False


MARKERS = ("failed", "blocked")
# A third file beside the outputs, `<stage>.review.json`, written when the stage is done but check_outputs.py
# found something to look at (2026-10-23). Not a MARKERS kind: it stops nothing, dispatch ignores it, and
# `retry` on a done stage that holds one redoes the stage (outputs archived under .redo/) when nothing
# later has used them. The hosted supervisor reads it from the outcome; a person reads it on the report.
REVIEW = "review"
# What a redo sets aside besides the stage's `produces` files: the directories a stage's script treats
# as its cache, so the stage does run again rather than find everything already made.
REDO_ALSO = {"design": ["design"], "copy": ["social/posts.json"], "research": ["research"]}


def marker(wd: Path, stage: str, kind: str) -> Path:
    return wd / f"{stage}.{kind}.json"


def read_marker(wd: Path, stage: str) -> tuple[str, dict] | None:
    """(kind, contents) of the stage's marker, when one is there."""
    for kind in MARKERS:
        m = marker(wd, stage, kind)
        if m.exists():
            try:
                return kind, json.loads(m.read_text())
            except (OSError, json.JSONDecodeError):
                return kind, {}
    return None


def write_marker(wd: Path, stage: str, kind: str, run: str, attempt: int, text: str, extra: dict | None = None) -> None:
    """`extra` carries what helps a reader of the marker: the worker's transcript path (`worker_log`, the
    runs/<run>/workers/ file the director kept, 2026-10-23) and the script's error code when it gave one."""
    clear_markers(wd, stage)
    doc = {"stage": stage, "status": kind, "run": run, "attempt": attempt,
           ("question" if kind == "blocked" else "reason"): str(text or f"{kind} with no reason given"), "at": now(),
           **{k: str(v) for k, v in (extra or {}).items() if v},
           "clear": "delete this file (or Try again in the console) to let a run dispatch the stage again"}
    marker(wd, stage, kind).write_text(json.dumps(contract_check(doc, "marker"), indent=2))


def write_review(wd: Path, stage: str, run: str, attempt: int, problems: list[str], worker_log: str | None = None) -> None:
    doc = {"stage": stage, "status": REVIEW, "run": run, "attempt": attempt, "problems": [str(p) for p in problems], "at": now(),
           **({"worker_log": str(worker_log)} if worker_log else {}),
           "note": "the stage is done and the run went on; a supervisor or a person decides whether to redo it "
                   "(ledger.py retry redoes a done stage that holds this file while nothing later has used its outputs)"}
    marker(wd, stage, REVIEW).write_text(json.dumps(contract_check(doc, "marker"), indent=2))


def clear_markers(wd: Path, stage: str) -> None:
    for kind in (*MARKERS, REVIEW):
        marker(wd, stage, kind).unlink(missing_ok=True)


def set_aside(wd: Path, st: dict, tag: str) -> list[str]:
    """A stage's outputs moved under .redo/<stage>-<time>[-<tag>]/, never deleted: before a redo, and when
    `record` found the output unusable (left in place, `refresh` would read the file as the stage done)."""
    keep = wd / ".redo" / (f"{st['stage']}-{now().replace(':', '')}" + (f"-{tag}" if tag else ""))
    moved: list[str] = []
    for pattern in [*st["produces"], *REDO_ALSO.get(st["stage"], [])]:
        for p in sorted(wd.glob(pattern)) or ([wd / pattern] if (wd / pattern).exists() else []):
            dest = keep / p.relative_to(wd)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest))
            moved.append(str(p.relative_to(wd)))
    return moved


def requeue(rec: dict, why: str) -> None:
    """A stopped stage queued again with fresh attempts. What it ended with stays as `previous`, so the
    next brief carries it, and `retries` counts how often this happened: a supervisor reading the same
    `previous` behind a growing count knows the retry proved nothing."""
    prior = rec.get("reason") or rec.get("question") or rec.get("previous")
    for k in ("reason", "question", "summary", "finished", "dispatched", "agent", "cached"):
        rec.pop(k, None)
    rec.update({"status": "queued", "attempt": 0, "previous": prior,
                "retries": int(rec.get("retries") or 0) + 1, "retried": now(), "retried_why": why})


def unfinished(wd: Path, stages: dict) -> bool:
    """True when an enabled stage still lacks its output and nothing marks the item as stopped."""
    if (wd / ".lock").exists() and not lock_is_stale(wd):
        return False  # a live worker holds it; another run will pick it up when it is free
    for st in stages["stages"]:
        if not st.get("enabled", True):
            continue
        if all(exists(wd, p) for p in st["produces"]):
            continue
        # a stage whose own hold or error file exists is stopped, not pending
        if read_marker(wd, st["stage"]):
            return False
        if st["stage"] == "price" and (wd / "price.rejected.json").exists():
            return False
        if st["stage"] == "quote" and (wd / "quote.error.json").exists():
            return False
        if st["stage"] == "analyze" and (wd / "analysis.error.json").exists():
            return False
        # waiting on a person's approval file is a stop, not pending work
        if any(r.endswith(".approved.json") and not exists(wd, r) for r in st["requires"]):
            return False
        return True
    return False


def existing_items(a, stages: dict) -> list[str]:
    """Work-dir hashes to register besides the folder: --items, or --resume for every unfinished one."""
    work = root() / "work"
    hashes = []
    if a.items:
        for h in a.items.split(","):
            h = h.strip()
            if h and (work / h / "meta.json").exists():
                hashes.append(h)
    if a.resume and work.exists():
        for d in sorted(work.iterdir()):
            if d.is_dir() and (d / "meta.json").exists() and unfinished(d, stages):
                hashes.append(d.name)
    return sorted(set(hashes))


def thumbnails(wd: Path, state: dict) -> None:
    """A 3MF's own previews, copied to thumbnails/ at registration: free pictures for the research worker."""
    try:
        extract_thumbnails(wd)
    except Exception as e:  # a bad zip is the quote stage's problem to report, not the ledger's
        state.setdefault("notes", []).append(f"{now()} {wd.name[:8]}: thumbnails not extracted: {e}")


ASSEMBLY = Path(__file__).resolve().parent / "assembly_3mf.py"


def assembly(wd: Path, state: dict, original: Path | None = None) -> None:
    """A 3MF's project files, read at registration, before anything strips them: assembly.json (placement per
    part, extruder and filament colour per part, painted extruders, the filament list) and display.ply for
    the renderers. Runs on the file the person gave when there is one, else on the work dir's source. Writes
    nothing for an STL, for a 3MF with neither an assemble block nor a second colour, or when assembly.json
    is already there. Added 2026-10-15: only the console upload read these, so a quickstart run never knew a
    file was multicolour and rendered, designed and wrote it in one colour."""
    src = original if original is not None else next(iter(wd.glob("source.*")), None)
    out_p = wd / "assembly.json"
    if src is None or src.suffix.lower() != ".3mf" or out_p.exists():
        return
    try:
        r = subprocess.run([sys.executable, str(ASSEMBLY), "read", str(src), "--out", str(out_p)],
                           capture_output=True, text=True, timeout=300)
        doc = json.loads(out_p.read_text()) if r.returncode == 0 and out_p.exists() else None
        if not doc or not (doc.get("source") or doc.get("multicolour")):
            out_p.unlink(missing_ok=True)
            return
        b = subprocess.run([sys.executable, str(ASSEMBLY), "build", "--workdir", str(wd)],
                           capture_output=True, text=True, timeout=600)
        if b.returncode != 0:
            state.setdefault("notes", []).append(f"{now()} {wd.name[:8]}: display.ply not built: {(b.stderr or b.stdout).strip()[-160:]}")
    except Exception as e:  # a bad zip is the quote stage's problem to report; the ledger only loses the colours
        out_p.unlink(missing_ok=True)
        state.setdefault("notes", []).append(f"{now()} {wd.name[:8]}: assembly not read: {e}")


def describe(wd: Path, text: str | None) -> None:
    """What the seller wrote about the object when they uploaded it, into meta.json as `description`, once:
    the first words stay (a later run of the same file without any says nothing new). Optional everywhere;
    the research skill reads it as a witness beside the filename."""
    text = (text or "").strip()
    if not text:
        return
    p = wd / "meta.json"
    meta = json.loads(p.read_text()) if p.exists() else {}
    if meta.get("description"):
        return
    meta["description"] = text[:2000]
    p.write_text(json.dumps(meta, indent=2))


def cmd_init(a) -> int:
    cfg = load_config()
    stages = load_stages(a.stages)
    folder = Path(a.folder) if a.folder else None
    if folder and not (folder.is_dir() or (folder.is_file() and folder.suffix.lower() in SUPPORTED)):
        return out({"ok": False, "reason": f"not a folder or a model file ({', '.join(sorted(SUPPORTED))}): {folder}"}, 1)
    if not folder and not a.items and not a.resume:
        return out({"ok": False, "reason": "give --folder, --items or --resume"}, 1)
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
    for h in existing_items(a, stages):
        wd = root() / "work" / h
        meta = json.loads((wd / "meta.json").read_text())
        if h not in state["items"]:
            state["items"][h] = {"file": meta.get("file", h), "workdir": str(wd.relative_to(root())), "stages": {}}
            added.append(h)
        describe(wd, getattr(a, "description", None))
        thumbnails(wd, state)
        assembly(wd, state)
    # A folder of models, or one model file: a person drops one file far more often than a folder.
    for f in ([] if not folder else [folder] if folder.is_file() else sorted(folder.iterdir())):
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
        describe(wd, getattr(a, "description", None))
        thumbnails(wd, state)
        assembly(wd, state, f)
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
            # A running stage belongs to its worker until `record` hears from it or
            # it goes stale: its output appearing early is expected, not completion.
            # Flipping it here made every record arrive at a "done" stage and be
            # rejected, so no cost or summary was kept (seen 2026-09-09).
            if all(exists(wd, p) for p in st["produces"]) and rec["status"] not in ("done", "running"):
                rec.update({"status": "done", "finished": rec.get("finished") or now()})
                if "summary" not in rec:
                    rec["summary"] = "output present on disk"
                clear_markers(wd, st["stage"])   # the outputs are the fact; an old marker is history
            elif rec["status"] == "running":
                started = datetime.fromisoformat(rec["dispatched"])
                age = (datetime.now(timezone.utc) - started).total_seconds() / 60
                if age > max_min:
                    rec["status"] = "queued" if rec["attempt"] < state["budget"]["max_attempts"] else "failed"
                    rec["previous"] = f"attempt {rec['attempt']} exceeded {max_min} minutes with no result"
                    state["notes"].append(f"{now()} {h[:8]} {st['stage']}: stale running stage reset")
                    if rec["status"] == "failed":
                        rec["reason"] = rec["previous"]
                        write_marker(wd, st["stage"], "failed", state["run"], rec["attempt"], rec["previous"])
            elif rec["status"] == "queued":
                # A marker left by an earlier run holds until a person clears it: this run does not retry it.
                held = read_marker(wd, st["stage"])
                if held:
                    kind, doc = held
                    rec["status"] = kind
                    rec[("question" if kind == "blocked" else "reason")] = doc.get("question") or doc.get("reason") or f"{kind} in run {doc.get('run')}"
                    rec["summary"] = rec.get("summary") or f"{kind} in run {doc.get('run')}, held by {marker(wd, st['stage'], kind).name}"
            elif rec["status"] in MARKERS and not read_marker(wd, st["stage"]):
                # The marker is the fact and it is gone: someone cleared it (rm, or `retry` on another run's
                # state), so this run takes the stage up again instead of remembering the stop forever.
                requeue(rec, "marker cleared")
                state["notes"].append(f"{now()} {h[:8]} {st['stage']}: marker cleared; queued again")


def ready_briefs(state: dict, stages: dict) -> list[dict]:
    briefs = []
    for h, item in state["items"].items():
        wd = root() / item["workdir"]
        clear_stale_lock(wd, None, state.setdefault("notes", []))
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
                write_marker(wd, st["stage"], "failed", state["run"], rec["attempt"], rec["reason"])
                break
            briefs.append({
                "run": state["run"], "department": state["department"], "item": h,
                "workdir": item["workdir"], "file": item["file"], "stage": st["stage"], "skill": st["skill"],
                "requires": st["requires"], "produces": st["produces"], "gate": st["gate"],
                **({"agent": st["agent"]} if st.get("agent") else {}),
                # A stage with no judgement in it names a script; run_intake.py runs it instead of a worker.
                **({"script": st["script"]} if st.get("script") else {}),
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
    if getattr(a, "peek", False):
        # a dry run must not claim stages, or the next real run finds them "running"
        return out({"ok": True, "briefs": briefs, "remaining": remaining(state), "peek": True})
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
    # The worker that held this stage has ended, however it ended. A lock it
    # left behind (killed on budget or time) would park the item until stale.
    (wd / ".lock").unlink(missing_ok=True)
    rec["summary"] = res["summary"]
    rec["agent"] = res["agent"]
    rec["finished"] = now()
    extra = {"worker_log": a.worker_log, "error": res.get("error")}
    if a.worker_log:
        rec["worker_log"] = a.worker_log
    rec.pop("review", None)
    if getattr(a, "interrupted", False):
        # The director was told to stop (SIGTERM on a deploy) while this worker ran: not the worker's
        # failure and not an attempt spent. Queued again where it was; the next director takes it up.
        rec["status"] = "queued"
        rec["attempt"] = max(0, rec["attempt"] - 1)
        rec["previous"] = res["reason"] or "interrupted"
        state["notes"].append(f"{now()} {res['item'][:8]} {res['stage']}: interrupted, attempt not counted")
    elif res["needs_human"]:
        rec["status"] = "blocked"
        rec["question"] = res["needs_human"]
        write_marker(wd, res["stage"], "blocked", a.run, rec["attempt"], res["needs_human"], extra)
    elif res["ok"]:
        stages = load_stages(state.get("stages_file"))
        st = next(s for s in stages["stages"] if s["stage"] == res["stage"])
        missing = [p for p in st["produces"] if not exists(wd, p)]
        checks = check_outputs(wd, res["stage"]) if not missing else {"hard": [], "soft": []}
        if missing or checks["hard"]:
            why = (f"claimed ok but outputs missing: {missing}" if missing
                   else "claimed ok but the output is not usable: " + "; ".join(checks["hard"]))
            rec["status"] = "queued" if rec["attempt"] < state["budget"]["max_attempts"] else "failed"
            rec["previous"] = why
            state["notes"].append(f"{now()} {res['item'][:8]} {res['stage']}: {why}")
            if checks["hard"]:
                set_aside(wd, st, "unusable")
            if rec["status"] == "failed":
                rec["reason"] = rec["previous"]
                write_marker(wd, res["stage"], "failed", a.run, rec["attempt"], rec["previous"], extra)
        else:
            rec["status"] = "done"
            rec["cached"] = res["cached"]
            clear_markers(wd, res["stage"])
            if checks["soft"]:
                rec["review"] = checks["soft"]
                write_review(wd, res["stage"], a.run, rec["attempt"], checks["soft"], a.worker_log)
                state["notes"].append(f"{now()} {res['item'][:8]} {res['stage']}: done, for review: {'; '.join(checks['soft'])}")
    else:
        rec["previous"] = res["reason"]
        rec["status"] = "queued" if rec["attempt"] < state["budget"]["max_attempts"] else "failed"
        if rec["status"] == "failed":
            rec["reason"] = res["reason"]
            write_marker(wd, res["stage"], "failed", a.run, rec["attempt"], res["reason"], extra)
    if a.tokens or a.usd is not None:
        cfg = load_config()
        price = cfg.get("pricing_usd_per_1k_tokens", {}).get(a.model or "inherit", 0.0)
        usd = a.usd if a.usd is not None else (a.tokens or 0) / 1000 * price
        c = state["cost"]
        c["tokens"] += a.tokens or 0
        c["usd"] = round(c["usd"] + usd, 4)
        by = c["by_agent"].setdefault(res["agent"], {"tokens": 0, "usd": 0.0, "results": 0})
        by["tokens"] += a.tokens or 0
        by["usd"] = round(by["usd"] + usd, 4)
        by["results"] += 1
        rec["tokens"] = a.tokens or 0
        rec["usd"] = round(usd, 4)
    if a.model:
        rec["model"] = a.model
    # Every attempt, kept: the stage is the unit a person is billed in, and a repeated stage is the
    # second line of the same stage, not a hidden "turn" (2026-10-23). The dashboard sums these.
    rec.setdefault("attempts", []).append({
        "attempt": rec["attempt"] + (1 if getattr(a, "interrupted", False) else 0), "status": rec["status"],
        "usd": round(float(a.usd), 4) if a.usd is not None else rec.get("usd", 0.0), "tokens": a.tokens or 0,
        "seconds": a.seconds, "model": a.model, "finished": rec["finished"],
        **({"worker_log": a.worker_log} if a.worker_log else {}),
        **({"interrupted": True} if getattr(a, "interrupted", False) else {})})
    refresh_spend(state)
    save_state(state)
    return out({"ok": True, "item": res["item"], "stage": res["stage"], "status": rec["status"], "spend": rec.get("spend"),
                **({"review": rec["review"]} if rec.get("review") else {}), "remaining": remaining(state)})


# ---------------------------------------------------------------- status

def cmd_status(a) -> int:
    """Read-only: anyone may poll it while a director is mid-pass."""
    state = load_state(a.run)
    stages = load_stages(state.get("stages_file"))
    refresh(state, stages)
    ready = len(ready_briefs(state, stages))
    r = remaining(state)
    over = state["budget"].get("usd_per_run") is not None and state["cost"]["usd"] > state["budget"]["usd_per_run"]
    return out({"ok": True, "run": a.run, "counts": r, "ready": ready, "cost": state["cost"],
                "over_budget": over, "work_left": (ready + r["running"]) > 0 and not over})


# ----------------------------------------------------------------- retry

def cmd_retry(a) -> int:
    """A person, the console or the hosted runner takes a stopped stage up again: the stage's marker is
    cleared and its record in this run is queued with fresh attempts (`requeue`). The next `next` dispatches
    it, with what it ended with as `previous`. Refused while a live worker holds the item, for a stage the
    table does not know, and for a stage that is done: outputs are never regenerated from here; a person
    deletes them to force a redo. A stage with no marker and no record in this run is nothing to retry."""
    wd = root() / "work" / a.item
    if not (wd / "meta.json").exists():
        return out({"ok": False, "reason": f"no such item: {a.item}"}, 1)
    state_p = run_dir(a.run) / "state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else None
    stages = load_stages(state.get("stages_file") if state else None)
    if a.stage not in {s["stage"] for s in stages["stages"]}:
        return out({"ok": False, "reason": f"unknown stage {a.stage!r}"}, 1)
    if (wd / ".lock").exists() and not lock_is_stale(wd):
        return out({"ok": False, "reason": "a worker holds this item; retry when its stage ends"}, 1)
    rec = ((state or {}).get("items", {}).get(a.item) or {}).get("stages", {}).get(a.stage)
    was = rec["status"] if rec else None
    archived: list[str] = []
    st = next(s for s in stages["stages"] if s["stage"] == a.stage)
    done_on_disk = all(exists(wd, p) for p in st["produces"])
    if was == "done" or (was is None and done_on_disk):
        # A done stage is redone only on purpose: a review marker says a check found something, or a
        # person says --redo. Its outputs are set aside, never deleted, and only while no later stage
        # has read them: a redo under a done price would leave the price resting on files that are gone.
        if not (marker(wd, a.stage, REVIEW).exists() or getattr(a, "redo", False)):
            return out({"ok": False, "reason": f"{a.stage} is done on this item and holds no review marker; pass --redo to redo it"}, 1)
        later = [s["stage"] for s in stages["stages"][stages["stages"].index(st) + 1:]
                 if s.get("enabled", True) and (all(exists(wd, p) for p in s["produces"])
                                                or ((state or {}).get("items", {}).get(a.item, {}).get("stages", {}).get(s["stage"], {}).get("status") == "running"))]
        if later:
            return out({"ok": False, "reason": f"{a.stage} is done and {', '.join(later)} already used its outputs; a redo would strand them"}, 1)
        archived = set_aside(wd, st, "")
    cleared = [marker(wd, a.stage, kind).name for kind in (*MARKERS, REVIEW) if marker(wd, a.stage, kind).exists()]
    if not cleared and not archived and was not in MARKERS:
        return out({"ok": False, "reason": f"nothing to retry: {a.stage} has no marker and is {was or 'not in this run'}"}, 1)
    clear_markers(wd, a.stage)
    who = f"{a.by}: " if a.by else ""
    why = f"{who}{a.reason or 'retry'}"
    retries = None
    if rec is not None:
        requeue(rec, why)
        rec.pop("review", None)
        retries = rec["retries"]
    if state is not None:
        state.setdefault("notes", []).append(
            f"{now()} {a.item[:8]} {a.stage}: {'redone' if archived else 'retried'} ({why}); was {was or 'unrecorded'}; "
            f"cleared {cleared or 'no marker'}" + (f"; set aside {archived}" if archived else ""))
        save_state(state)
    return out({"ok": True, "run": a.run, "item": a.item, "stage": a.stage, "was": was, "cleared": cleared,
                "archived": archived, "retries": retries})


# ---------------------------------------------------------------- outcome

def read_json(p: Path) -> dict | None:
    """The file as an object, or None when it is missing, empty or not JSON. The one place that forgives."""
    try:
        doc = json.loads(p.read_text() or "null")
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def media_of(wd: Path) -> dict:
    """What a reader can show of an item, as keys relative to the project root (the paths the hosted sync
    uses under the seller's prefix): the 3MF's own thumbnail, the preview drafts, the design photos in
    listing order and the listing clip, each only when the file exists. Empty lists and nulls are the
    common case early in a run. Keys, never URLs: the backend presigns them for the browser."""
    rel = lambda p: str(p.relative_to(root()))  # noqa: E731
    doc: dict = {"name": None, "thumbnail": None, "drafts": [], "photos": [], "video": None}
    for cand in ("thumbnails/plate_1.png", "thumbnails/top_1.png"):
        if (wd / cand).exists():
            doc["thumbnail"] = rel(wd / cand)
            break
    doc["drafts"] = [rel(p) for p in sorted((wd / "renders").glob("draft_*.png"))] if (wd / "renders").is_dir() else []
    design = read_json(wd / "design.json") or {}
    doc["name"] = design.get("name") if isinstance(design.get("name"), str) else None
    doc["photos"] = [{"id": ph.get("id"), "kind": ph.get("kind"), "key": rel(wd / ph["file"])}
                     for ph in design.get("photos") or [] if isinstance(ph, dict) and ph.get("file") and (wd / ph["file"]).exists()]
    video = design.get("video") or {}
    if isinstance(video, dict) and video.get("file") and (wd / video["file"]).exists():
        doc["video"] = rel(wd / video["file"])
    return doc


MARKER_FIELDS = ("reason", "question", "run", "attempt", "worker_log", "error", "problems")


# ---------------------------------------------------------------- spend

def listing_fee() -> float:
    """Etsy's fee for activating a listing, from the store's fee table, else the plugin's, else 0.20 USD."""
    store = Path(os.environ.get("DAPILOT_STORE_DIR") or (root() / "store"))
    for p in (store / "reference" / "platform-fees.json",
              Path(__file__).resolve().parent.parent / "skills" / "research-comparables" / "reference" / "platform-fees.json"):
        fee = (((read_json(p) or {}).get("platforms") or {}).get("etsy") or {}).get("listing_fee")
        if isinstance(fee, (int, float)):
            return float(fee)
    return 0.20


def stage_spend(wd: Path, stage: str, rec: dict) -> dict:
    """What one stage of one item has cost, in three kinds (2026-10-26). `workers` is every attempt's
    Claude cost, as before. `assets` is the design stage's fal spend, read from design.json (or the
    produce manifest of an unfinished design): the sum of the paid requests in the work dir, so it is
    the item's total and not added up again across attempts. `fees` is Etsy's listing fee, counted for
    the publish stage only when published.json says the listing is live and active; a draft or a sandbox
    publish costs nothing. The ledger is the one reader of these files; everyone else sees the result."""
    workers = round(sum(float(a.get("usd") or 0.0) for a in rec.get("attempts") or []), 4)
    assets = fees = 0.0
    if stage == "design":
        design = read_json(wd / "design.json") or {}
        for doc in (design, design.get("spend") or {}, (read_json(wd / "design" / "produced.json") or {}).get("spend") or {}):
            v = doc.get("spend_usd", doc.get("usd"))
            if isinstance(v, (int, float)) and v > 0:
                assets = round(float(v), 4)
                break
    if stage == "publish":
        pub = read_json(wd / "published.json") or {}
        if not pub.get("sandbox") and ((pub.get("etsy") or {}).get("state") == "active"):
            fees = round(listing_fee(), 4)
    return {"workers": workers, "assets": assets, "fees": fees, "total": round(workers + assets + fees, 4)}


def item_spend(stages: dict) -> dict:
    """An item's spend: its stages' added up."""
    t = {"workers": 0.0, "assets": 0.0, "fees": 0.0}
    for rec in stages.values():
        sp = rec.get("spend") or {}
        for k in t:
            t[k] += float(sp.get(k) or 0.0)
    t = {k: round(v, 4) for k, v in t.items()}
    return {**t, "total": round(sum(t.values()), 4)}


def refresh_spend(state: dict) -> None:
    """Every stage's and item's spend from disk, and the run's cost totals: `usd` stays the workers'
    cost, as it always was; `assets_usd`, `fees_usd` and `total_usd` are the rest. Called at record and
    when the outcome is built, so a design.json or published.json that appeared later is counted."""
    assets = fees = 0.0
    for it in state["items"].values():
        wd = root() / it["workdir"]
        for stage, rec in it.get("stages", {}).items():
            if rec.get("attempts") or rec.get("status") == "done":
                rec["spend"] = stage_spend(wd, stage, rec)
        sp = item_spend(it.get("stages", {}))
        it["spend"] = sp
        assets += sp["assets"]
        fees += sp["fees"]
    c = state["cost"]
    c["assets_usd"], c["fees_usd"] = round(assets, 4), round(fees, 4)
    c["total_usd"] = round(float(c.get("usd") or 0.0) + assets + fees, 4)


def _num(v):
    return round(float(v), 2) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def gate_market(wd: Path, cands: dict | None) -> dict:
    """What a person weighs at the gate besides the proposal (2026-10-26): the market band research found,
    the cost 3DAPI quoted, the floor, the verdict in research's words, the candidates the script rejected
    and why, and the heaviest comparable listings with their links. Read from price.candidates.json (or
    price.rejected.json), viability.json and comparables.json; every field null when its file is missing,
    so a gate is never withheld for want of context."""
    cands = cands or {}
    viab = read_json(wd / "viability.json") or {}
    comps = read_json(wd / "comparables.json") or {}
    market = cands.get("market") if isinstance(cands.get("market"), dict) else (viab.get("market") if isinstance(viab.get("market"), dict) else {})
    offer = cands.get("offer") if isinstance(cands.get("offer"), dict) else {}
    rows = [r for r in (comps.get("comparables") or []) if isinstance(r, dict)] if isinstance(comps.get("comparables"), list) else []
    rows.sort(key=lambda r: -(float(r.get("market_weight") or 0) if isinstance(r.get("market_weight"), (int, float)) else 0))
    similar = []
    for r in rows:
        price = _num(r.get("unit_price")) or _num(r.get("price"))
        if price is None or not isinstance(r.get("url"), str):
            continue
        shop = r.get("shop") if isinstance(r.get("shop"), dict) else {}
        similar.append({"title": str(r.get("title") or "")[:140], "price": price,
                        "currency": str(r.get("currency") or cands.get("currency") or "USD"), "url": r["url"],
                        "shop": shop.get("name") if isinstance(shop.get("name"), str) else None,
                        "sold": shop.get("sold") if isinstance(shop.get("sold"), int) else None,
                        "reviews": r.get("reviews") if isinstance(r.get("reviews"), int) else None})
        if len(similar) == 6:
            break
    return {
        "currency": str(cands.get("currency") or market.get("currency") or "USD"),
        "n": market.get("n") if isinstance(market.get("n"), int) else None,
        **{k: _num(market.get(k)) for k in ("min", "p25", "median", "p75", "max")},
        "thin": next((bool(src["thin"]) for src in (market, viab.get("market") if isinstance(viab.get("market"), dict) else {})
                      if isinstance(src.get("thin"), bool)), None),
        "unit_cost": _num(cands.get("unit_cost")) or _num(cands.get("quote_unit_price")),
        "offer_cost": _num(cands.get("offer_cost")),
        "units": offer.get("units") if isinstance(offer.get("units"), int) else None,
        "floor": _num(cands.get("floor")),
        "verdict": viab.get("verdict") if isinstance(viab.get("verdict"), str) else (cands.get("verdict") if isinstance(cands.get("verdict"), str) else None),
        "verdict_reason": viab.get("verdict_reason") if isinstance(viab.get("verdict_reason"), str) else None,
        "rejected_candidates": [{"label": str(c.get("label") or ""), "price": _num(c.get("price")), "because": str(c.get("rejected_because") or "")}
                                for c in (cands.get("rejected") or []) if isinstance(c, dict) and _num(c.get("price")) is not None],
        "similar": similar,
    }


# ---------------------------------------------------------------- report and account

def account_of(wd: Path, stage: str) -> str | None:
    """The worker's own paragraph about the stage, from the work dir's log.md (output-format skill): the
    last one for the stage, since a retry appends another. Two spellings are in the wild, "**design**
    (product-designer): ..." and "**design (product-designer, attempt 1, failed):** ...". Capped."""
    try:
        text = (wd / "log.md").read_text(errors="replace")
    except OSError:
        return None
    found = None
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        m = re.match(r"^\*\*" + re.escape(stage) + r"(?:\*\*)?\s*\(([^)]*)\)(?:\*\*)?:?\*?\*?:?\s*(.*)$", para, re.S)
        if m:
            found = re.sub(r"\s+", " ", m.group(2)).strip()
    return found[:1500] if found else None


def _pick(d: dict | None, keys: tuple) -> dict:
    d = d or {}
    return {k: d.get(k) for k in keys if k in d}


def stage_report(wd: Path, stage: str) -> dict | None:
    """What the stage made, as the dashboard shows it (2026-10-26): a small object per stage read from the
    stage's own output files, so the words and figures leave the work dir with the outcome instead of
    staying on the runner. Null when the stage has produced nothing yet. Bounded: six comparables, the
    scenes as written, the listing text whole (Etsy caps it at 102 and 13 and a few thousand characters)."""
    if stage == "analyze":
        a = read_json(wd / "analysis.json")
        return _pick(a, ("bbox_mm", "volume_mm3", "area_mm2", "triangles", "bodies", "watertight", "watertight_bodies")) or None
    if stage == "quote":
        q = read_json(wd / "quote.json")
        if not q:
            return None
        m = q.get("metrics") if isinstance(q.get("metrics"), dict) else {}
        return {"public_id": q.get("publicId"), "status": q.get("status"), "unit_price": _num(q.get("unit_price")),
                "currency": "USD", **_pick(m, ("model_g", "total_g", "print_time_s", "filament_count")),
                "error": q.get("errorMessage")}
    if stage == "preview":
        r = (read_json(wd / "renders" / "render.json") or {}).get("draft")
        return _pick(r, ("frames", "res", "wall_s", "studio")) or None
    if stage == "research":
        comps = read_json(wd / "comparables.json")
        viab = read_json(wd / "viability.json")
        if not comps and not viab:
            return None
        comps, viab = comps or {}, viab or {}
        market = gate_market(wd, read_json(wd / "price.candidates.json"))
        ident = comps.get("identity") if isinstance(comps.get("identity"), dict) else {}
        return {"product": comps.get("product") or viab.get("product"), "queries": comps.get("queries"),
                "taxonomy_id": comps.get("taxonomy_id"), "identity": _pick(ident, ("object", "confidence", "reason", "witnesses")),
                "n_scored": comps.get("n_scored"), "n_selected": comps.get("n_selected"),
                "verdict": viab.get("verdict"), "verdict_reason": viab.get("verdict_reason"),
                "offer": _pick(viab.get("offer") if isinstance(viab.get("offer"), dict) else {}, ("units", "note")),
                "market": {k: market.get(k) for k in ("currency", "n", "min", "p25", "median", "p75", "max", "thin")},
                "similar": market.get("similar") or []}
    if stage == "price":
        pr = read_json(wd / "price.json")
        rej = read_json(wd / "price.rejected.json")
        appr = read_json(wd / "price.approved.json")
        cands = read_json(wd / "price.candidates.json") or {}
        if not pr and not rej and not appr:
            return None
        return {**_pick(pr, ("price", "currency", "candidate", "position", "rationale", "floor", "verdict")),
                "rejected": (rej or {}).get("reason") if rej else None,
                "floor": _num((pr or {}).get("floor")) or _num(cands.get("floor")),
                "unit_cost": _num(cands.get("unit_cost")) or _num(cands.get("quote_unit_price")),
                "candidates": [{"label": c.get("label"), "price": _num(c.get("price")), "margin_pct": _num(c.get("margin_pct"))}
                               for c in (cands.get("candidates") or []) if isinstance(c, dict)],
                "approved": _pick(appr, ("price", "currency", "by", "at", "approved_by", "approved_at")) or None}
    if stage == "design":
        d = read_json(wd / "design.json")
        b = read_json(wd / "design" / "brief.json") or {}
        if not d and not b:
            return None
        d = d or {}
        std = d.get("standard") if isinstance(d.get("standard"), dict) else {}
        return {"name": d.get("name") or b.get("name"), "reason": d.get("reason") or b.get("reason"),
                "color": d.get("color") or b.get("color"), "hero_yaw": d.get("hero_yaw", b.get("hero_yaw")),
                "scenes": [str(x.get("scene") or "") for x in (b.get("lifestyle_images") or []) if isinstance(x, dict)],
                "clip_scene": (b.get("lifestyle_video") or {}).get("scene") if isinstance(b.get("lifestyle_video"), dict) else None,
                "standard_met": std.get("met"), "photos": len(d.get("photos") or []), "video": bool(d.get("video")),
                "rejected": d.get("rejected") or [], "redone": d.get("redone") or [], "warnings": d.get("warnings") or [],
                "finished": d.get("finished")}
    if stage == "copy":
        l = read_json(wd / "listing.json")
        return _pick(l, ("title", "description", "tags", "materials", "taxonomy_id", "written_by")) or None
    if stage == "publish":
        pub = read_json(wd / "published.json")
        if not pub:
            return None
        etsy = pub.get("etsy") if isinstance(pub.get("etsy"), dict) else {}
        return {"sandbox": bool(pub.get("sandbox")), "at": pub.get("at"), "approved_by": pub.get("approved_by"),
                "etsy": _pick(etsy, ("state", "listing_id", "url")),
                "posted": [f"{x.get('platform')}.{x.get('format')}" if isinstance(x, dict) else str(x) for x in (pub.get("social") or pub.get("posted") or [])],
                "skipped": pub.get("skipped") or [], "failed": pub.get("failed") or []}
    return None


def gate_of(wd: Path) -> dict | None:
    """The seller's turn: price.json (a proposal) or price.rejected.json (no candidate cleared the floor;
    the person may still set a price above it) without price.approved.json. Before 2026-10-26 only the
    proposal opened the gate, so a rejected item showed as a question with no way to answer it."""
    if (wd / "price.approved.json").exists():
        return None
    proposal_p, rejected_p = wd / "price.json", wd / "price.rejected.json"
    if not proposal_p.exists() and not rejected_p.exists():
        return None
    cands = read_json(wd / "price.candidates.json") or read_json(rejected_p)
    rejected = read_json(rejected_p) if rejected_p.exists() and not proposal_p.exists() else None
    return {"proposal": read_json(proposal_p) if proposal_p.exists() else None, "candidates": cands,
            "market": gate_market(wd, cands),
            "rejected": (str(rejected.get("reason")) if rejected and rejected.get("reason") else None)}


def build_outcome(run: str) -> dict:
    """The run as one document (schema/outcome.schema.json). A run with no state yet, a director that
    refused before registering anything, is a valid outcome with `started` false and no items, so a
    reader has one path. Refreshes the state in memory like `status` does and saves nothing."""
    state_p = run_dir(run) / "state.json"
    if not state_p.exists():
        return contract_check({"run": run, "at": now(), "started": False, "counts": {}, "cost": None,
                               "gates_open": 0, "notes": [], "items": []}, "outcome")
    state = load_state(run)
    stages = load_stages(state.get("stages_file"))
    refresh(state, stages)
    refresh_spend(state)
    items = []
    for h, it in state["items"].items():
        wd = root() / it["workdir"]
        # every stage that has run carries what it made (`report`) and the worker's own paragraph (`account`)
        stages_out = {}
        for stage, st_rec in it.get("stages", {}).items():
            out_rec = dict(st_rec)
            if out_rec.get("status") != "queued" or out_rec.get("attempts"):
                report = stage_report(wd, stage)
                if report:
                    out_rec["report"] = report
                account = account_of(wd, stage)
                if account:
                    out_rec["account"] = account
            stages_out[stage] = out_rec
        rec = {"item": h, "file": it.get("file"), "stages": stages_out, "gate": None, "markers": [],
               "media": media_of(wd), "spend": it.get("spend")}
        rec["gate"] = gate_of(wd)
        # failed and blocked stop the item; review stops nothing, the stage is done and a check found something
        for m in sorted(wd.glob("*.failed.json")) + sorted(wd.glob("*.blocked.json")) + sorted(wd.glob("*.review.json")):
            doc = read_json(m) or {}
            rec["markers"].append({"file": m.name, "stage": doc.get("stage") or m.name.split(".")[0],
                                   **{k: doc.get(k) for k in MARKER_FIELDS}})
        items.append(rec)
    counts: dict[str, int] = {}
    for rec in items:
        for st in rec["stages"].values():
            counts[st.get("status", "queued")] = counts.get(st.get("status", "queued"), 0) + 1
    return contract_check({"run": run, "at": now(), "started": True, "counts": counts, "cost": state.get("cost"),
                           "gates_open": sum(1 for r in items if r["gate"]),
                           "notes": [str(n) for n in (state.get("notes") or [])[-20:]], "items": items}, "outcome")


def cmd_outcome(a) -> int:
    """Read-only, like status: the run as the dashboard, the runner and the supervisor read it."""
    return out(build_outcome(a.run))


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
    review = [(h, item["file"], s, "; ".join(rec["review"])) for h, item in state["items"].items()
              for s, rec in item["stages"].items() if rec.get("review")]
    if review:
        lines += ["", "## Done, for review", ""]
        lines += [f"- {h[:8]} {f} at {s}: {r}" for h, f, s, r in review]
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
    s = sub.add_parser("init"); s.add_argument("--run", required=True); s.add_argument("--folder", help="a folder of model files, or one model file")
    s.add_argument("--items", help="comma-separated work-dir hashes to register, no folder needed")
    s.add_argument("--resume", action="store_true", help="register every work dir with an enabled stage still to run")
    s.add_argument("--description", help="what the seller wrote about the object(s); into each item's meta.json, once")
    s.add_argument("--department", default="catalogue"); s.add_argument("--stages"); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("next"); s.add_argument("--run", required=True); s.add_argument("--max", type=int)
    s.add_argument("--peek", action="store_true", help="show the briefs without marking them running"); s.set_defaults(fn=cmd_next)
    s = sub.add_parser("record"); s.add_argument("--run", required=True); s.add_argument("--result", required=True)
    s.add_argument("--tokens", type=int); s.add_argument("--usd", type=float, help="exact cost if the runtime reports it; overrides the token price estimate")
    s.add_argument("--model", help="the worker's model, for the record; the price table when --usd is absent")
    s.add_argument("--seconds", type=float, help="the attempt's wall clock"); s.add_argument("--worker-log", help="the director's transcript file for this attempt")
    s.add_argument("--interrupted", action="store_true", help="the director was stopped while this worker ran: queued again, attempt not counted")
    s.set_defaults(fn=cmd_record)
    s = sub.add_parser("status"); s.add_argument("--run", required=True); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("outcome", help="the run as one document (schema/outcome.schema.json): items, stages, gates, markers, media")
    s.add_argument("--run", required=True); s.set_defaults(fn=cmd_outcome)
    s = sub.add_parser("report"); s.add_argument("--run", required=True); s.set_defaults(fn=cmd_report)
    s = sub.add_parser("retry", help="clear a stage's marker and queue it again in this run with fresh attempts")
    s.add_argument("--run", required=True); s.add_argument("--item", required=True, help="the work-dir hash")
    s.add_argument("--stage", required=True); s.add_argument("--reason", help="why a retry could end differently")
    s.add_argument("--by", help="who asked: a person's name, or the supervisor")
    s.add_argument("--redo", action="store_true", help="redo a done stage without a review marker: its outputs are set aside under .redo/"); s.set_defaults(fn=cmd_retry)
    a = ap.parse_args()
    try:
        return a.fn(a)
    except SystemExit as e:
        return int(e.code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
