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
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
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
    marker(wd, stage, kind).write_text(json.dumps(
        {"stage": stage, "status": kind, "run": run, "attempt": attempt,
         ("question" if kind == "blocked" else "reason"): text, "at": now(), **{k: v for k, v in (extra or {}).items() if v},
         "clear": "delete this file (or Try again in the console) to let a run dispatch the stage again"}, indent=2))


def write_review(wd: Path, stage: str, run: str, attempt: int, problems: list[str], worker_log: str | None = None) -> None:
    marker(wd, stage, REVIEW).write_text(json.dumps(
        {"stage": stage, "status": REVIEW, "run": run, "attempt": attempt, "problems": problems, "at": now(),
         **({"worker_log": worker_log} if worker_log else {}),
         "note": "the stage is done and the run went on; a supervisor or a person decides whether to redo it "
                 "(ledger.py retry redoes a done stage that holds this file while nothing later has used its outputs)"},
        indent=2))


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
    save_state(state)
    return out({"ok": True, "item": res["item"], "stage": res["stage"], "status": rec["status"],
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
