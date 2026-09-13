"""dapilot job API: the frontend's backend. The browser never talks to dapi.

  .venv/bin/uvicorn server.app:app --port 3100 --reload

POST /jobs (multipart `files`)     take the upload into the pipeline: strip foreign 3mf
                                   project settings, write work/<sha256>/, start a run
GET  /jobs                         every item known, newest first, stage and status from files
GET  /jobs/{id}                    one item with everything the work dir holds
GET  /jobs/{id}/events             one line per stage that produced its file, from mtimes
GET  /jobs/{id}/files/{path}       renders and json out of the work dir, read only
GET  /runs                         every run on disk, newest first, with counts, cost and whether it is live
GET  /runs/unfinished              work dirs with a stage still to run and no run holding them
POST /runs/resume                  start one director over all of those (also at boot when
                                   store/config.json has resume_on_start: true)
GET  /runs/{run}/report            the run's report.md
POST /jobs/{id}/approve            501 until a publish gate exists

Stage and status are never stored. They are read off the work dir against the
same stage table the ledger uses (run-intake/reference/stages.json), so the
UI and the pipeline cannot disagree. A `.lock` in the work dir means a worker
holds it; a live director process for the item means the next stage is
coming; otherwise the item waits for someone to start a run.

The pipeline itself is `run_intake.py`, one process per upload. Its workers
are Claude agents; this server holds no model and makes no judgement.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

ROOT = Path(os.environ.get("DAPILOT_PROJECT_DIR") or Path(__file__).resolve().parents[1])
PLUGIN = ROOT / "plugins" / "dapilot"
SCRIPTS = PLUGIN / "scripts"
STAGES_FILE = PLUGIN / "skills" / "run-intake" / "reference" / "stages.json"
WORK = ROOT / "work"
INTAKE = ROOT / "intake"
RUNS = ROOT / "runs"
SUPPORTED = {".stl", ".3mf", ".obj", ".step", ".stp"}
SERVABLE = {".png", ".jpg", ".mp4", ".json", ".md"}


def load_env() -> None:
    store = Path(os.environ.get("DAPILOT_STORE_DIR") or ROOT / "store")
    env = store / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    os.environ.setdefault("DAPILOT_STORE_DIR", str(store))
    os.environ.setdefault("DAPILOT_PROJECT_DIR", str(ROOT))


load_env()
PY = os.environ.get("DAPILOT_PYTHON") or sys.executable
app = FastAPI(title="dapilot job API")
DIRECTORS: dict[str, dict] = {}   # run id -> {proc, items, started, folder}
LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def stages() -> list[dict]:
    return [s for s in json.loads(STAGES_FILE.read_text())["stages"] if s.get("enabled", True)]


def read_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except ValueError:
        return None


def exists(wd: Path, pattern: str) -> bool:
    return any(wd.glob(pattern))


# ------------------------------------------------------------------ intake

def strip_if_project(path: Path) -> dict:
    """Foreign 3mf project settings make dapi's slicer fail; keep the geometry only."""
    if path.suffix.lower() != ".3mf":
        return {"stripped": False}
    check = subprocess.run([PY, str(SCRIPTS / "strip_3mf.py"), str(path), "--check"], capture_output=True, text=True)
    if check.returncode == 0:
        return {"stripped": False}
    if check.returncode != 2:
        raise HTTPException(400, f"{path.name}: {check.stderr.strip()[-300:] or 'not a readable 3mf'}")
    info = json.loads(check.stdout)
    out = path.with_name(path.stem + ".stripped.3mf")
    r = subprocess.run([PY, str(SCRIPTS / "strip_3mf.py"), str(path), str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        raise HTTPException(500, f"{path.name}: strip failed: {r.stderr.strip()[-300:]}")
    original_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    path.unlink()
    out.rename(path)
    return {"stripped": True, "original_sha256": original_sha, "application": info.get("application"),
            "printer_model": info.get("printer_model"), "removed": info.get("removed")}


def reject(folder: Path, message: str, status: int = 400) -> None:
    """A refused upload leaves no intake folder behind."""
    if folder.exists() and not any(folder.iterdir()):
        folder.rmdir()
    raise HTTPException(status, message)


def start_director(run: str, items: list[str], folder: Path | None = None, resume: bool = False) -> None:
    """One run_intake.py per run. Folder for fresh uploads, --items for work dirs already on disk."""
    log = RUNS / run / "director.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [PY, str(SCRIPTS / "run_intake.py"), "--run", run]
    if folder:
        cmd.append(str(folder))
    if resume:
        cmd.append("--resume")
    elif not folder and items:
        cmd += ["--items", ",".join(items)]
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=open(log, "a"), stderr=subprocess.STDOUT,
                            env={**os.environ, "DAPILOT_PROJECT_DIR": str(ROOT)})
    with LOCK:
        DIRECTORS[run] = {"proc": proc, "items": items, "started": now(), "folder": str(folder) if folder else None,
                          "log": str(log), "resume": resume}


def unfinished_items() -> list[str]:
    """Work dirs with an enabled stage still to run and nothing marking them stopped, as the ledger sees it."""
    r = subprocess.run([PY, str(SCRIPTS / "ledger.py"), "init", "--run", "_probe", "--resume"],
                       capture_output=True, text=True, cwd=ROOT)
    try:
        items = json.loads(r.stdout).get("added", [])
    except ValueError:
        items = []
    probe = RUNS / "_probe"
    if probe.exists():
        for f in probe.iterdir():
            f.unlink()
        probe.rmdir()
    return [i for i in items if not director_alive_for(i)]


def resume_all() -> dict:
    items = unfinished_items()
    if not items:
        return {"ok": True, "run": None, "items": []}
    run = "resume-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    start_director(run, items, resume=True)
    return {"ok": True, "run": run, "items": items}


@app.on_event("startup")
def resume_on_start() -> None:
    cfg = read_json(Path(os.environ["DAPILOT_STORE_DIR"]) / "config.json") or {}
    if cfg.get("resume_on_start"):
        r = resume_all()
        print(f"[server] resume_on_start: {len(r['items'])} unfinished item(s)" + (f" -> run {r['run']}" if r["run"] else ""))
    else:
        n = len(unfinished_items())
        if n:
            print(f"[server] {n} unfinished item(s) on disk; POST /runs/resume or set resume_on_start in store/config.json")


def worker_budget_minutes() -> float:
    cfg = read_json(Path(os.environ["DAPILOT_STORE_DIR"]) / "config.json") or {}
    base = float(cfg.get("max_worker_minutes", 15))
    return max([base, *(float(v) for v in (cfg.get("minutes_per_stage") or {}).values())])


def runs_on_disk() -> dict[str, dict]:
    """run id -> state.json, for every run under runs/ (the ledger's own record, whoever started it)."""
    out = {}
    if RUNS.exists():
        for d in RUNS.iterdir():
            if d.is_dir() and not d.name.startswith("_"):
                st = read_json(d / "state.json")
                if st:
                    out[d.name] = st
    return out


def run_is_live(run: str, state: dict | None = None) -> bool:
    """Live when this server holds its director, or its ledger shows a stage dispatched within the worker budget."""
    with LOCK:
        d = DIRECTORS.get(run)
        if d and d["proc"].poll() is None:
            return True
    state = state or read_json(RUNS / run / "state.json")
    if not state:
        return False
    budget = worker_budget_minutes() * 60 + 60
    for item in state.get("items", {}).values():
        for rec in item.get("stages", {}).values():
            if rec.get("status") == "running" and rec.get("dispatched"):
                try:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(rec["dispatched"])).total_seconds()
                except ValueError:
                    continue
                if age < budget:
                    return True
    return False


def director_alive_for(item: str) -> str | None:
    """The live run holding this item, from this server's directors or from the ledgers on disk."""
    with LOCK:
        for run, d in DIRECTORS.items():
            if item in d["items"] and d["proc"].poll() is None:
                return run
    for run, state in sorted(runs_on_disk().items(), reverse=True):
        if item in state.get("items", {}) and run_is_live(run, state):
            return run
    return None


def runs_for(item: str) -> list[str]:
    return sorted(run for run, st in runs_on_disk().items() if item in st.get("items", {}))


@app.post("/jobs")
async def create_jobs(files: list[UploadFile]) -> JSONResponse:
    if not files:
        raise HTTPException(400, "no files")
    upload_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + hashlib.sha1(os.urandom(8)).hexdigest()[:6]
    folder = INTAKE / upload_id
    folder.mkdir(parents=True, exist_ok=True)
    items: list[str] = []
    created: list[dict] = []
    for f in files:
        name = Path(f.filename or "upload").name
        ext = Path(name).suffix.lower()
        if ext not in SUPPORTED:
            reject(folder, f"unsupported: {ext or 'no extension'}")
        data = await f.read()
        if not data:
            reject(folder, f"{name}: empty file")
        safe = re.sub(r"[^A-Za-z0-9._+-]", "_", name)
        path = folder / safe
        path.write_bytes(data)
        strip = strip_if_project(path)
        item = hashlib.sha256(path.read_bytes()).hexdigest()
        wd = WORK / item
        wd.mkdir(parents=True, exist_ok=True)
        src = wd / f"source{ext}"
        if not src.exists():
            src.write_bytes(path.read_bytes())
        meta_p = wd / "meta.json"
        if not meta_p.exists():
            meta = {"file": name, "bytes": path.stat().st_size, "sha256": item, "first_seen": now(), "via": "frontend", **strip}
            meta_p.write_text(json.dumps(meta, indent=2))
        runs_p = wd / "runs.json"
        runs = read_json(runs_p) or []
        runs.append(f"frontend-{upload_id}")
        runs_p.write_text(json.dumps(runs, indent=2))
        items.append(item)
    start_director(f"frontend-{upload_id}", items, folder=folder)
    for item in items:
        created.append(job(item))
    return JSONResponse(created, status_code=201)


# ------------------------------------------------------------------ status from files

def status_of(item: str, wd: Path, meta: dict) -> dict:
    """Stage and status the way the ledger sees them, plus the reason when there is one."""
    lock = read_json(wd / ".lock") if (wd / ".lock").exists() else None
    run = director_alive_for(item)
    done_stages = []
    for st in stages():
        if all(exists(wd, p) for p in st["produces"]):
            done_stages.append(st["stage"])
            continue
        stage = st["stage"]
        # the stage's own failure or hold files
        if stage == "analyze" and (wd / "analysis.error.json").exists():
            a = read_json(wd / "analysis.error.json") or {}
            return {"stage": stage, "status": "failed", "reason": a.get("reason") or "analysis failed", "done": done_stages}
        if stage == "quote":
            if (wd / "quote.error.json").exists():
                q = read_json(wd / "quote.error.json") or {}
                return {"stage": "slice", "status": "failed", "reason": q.get("raw", {}).get("errorMessage") or q.get("status") or "slicer error",
                        "quoteId": q.get("publicId"), "done": done_stages}
            if (wd / "quote.pending.json").exists():
                q = read_json(wd / "quote.pending.json") or {}
                return {"stage": "slice", "status": "running", "reason": "slicing on dapi", "quoteId": q.get("publicId"), "done": done_stages}
        if stage == "price" and (wd / "price.rejected.json").exists():
            r = read_json(wd / "price.rejected.json") or {}
            return {"stage": stage, "status": "blocked", "reason": f"no sellable price: floor {r.get('floor')} against market median {r.get('market', {}).get('median')}", "done": done_stages}
        if lock and (lock.get("stage") == stage or not lock.get("stage")):
            return {"stage": "slice" if stage == "quote" else stage, "status": "running", "reason": None, "done": done_stages}
        if lock:
            return {"stage": "slice" if lock.get("stage") == "quote" else lock.get("stage"), "status": "running", "reason": None, "done": done_stages}
        if run:
            return {"stage": "slice" if stage == "quote" else stage, "status": "queued", "reason": f"next in run {run}", "done": done_stages}
        return {"stage": "slice" if stage == "quote" else stage, "status": "queued", "reason": "no run active for this item", "done": done_stages}
    last = done_stages[-1] if done_stages else "intake"
    return {"stage": "slice" if last == "quote" else last, "status": "done", "reason": None, "done": done_stages}


def job(item: str) -> dict | None:
    wd = WORK / item
    meta = read_json(wd / "meta.json")
    if not meta:
        return None
    s = status_of(item, wd, meta)
    j = {"id": item, "fileName": meta.get("file", item), "createdAt": meta.get("first_seen"),
         "updatedAt": iso(max((p.stat().st_mtime for p in wd.rglob("*") if p.is_file()), default=0)) if wd.exists() else meta.get("first_seen"),
         "stage": s["stage"], "status": s["status"], "reason": s.get("reason"), "stagesDone": s["done"],
         "stripped": bool(meta.get("stripped")), "sourceApplication": meta.get("application"), "runs": runs_for(item)}
    q = read_json(wd / "quote.json")
    if q:
        j.update(price=q.get("unit_price"), quoteId=q.get("publicId"), batch=q.get("batch"), metrics=q.get("metrics"))
    elif s.get("quoteId"):
        j["quoteId"] = s["quoteId"]
    a = read_json(wd / "analysis.json")
    if a:
        j["analysis"] = {k: a.get(k) for k in ("bbox_mm", "volume_mm3", "bodies", "watertight_bodies", "triangles")}
    return j


def detail(item: str) -> dict:
    j = job(item)
    if not j:
        raise HTTPException(404, "no such job")
    wd = WORK / item
    v = read_json(wd / "viability.json")
    if v and v.get("ok"):
        s0 = (v.get("scenarios") or [{}])[0]
        j["viability"] = {"verdict": v.get("verdict"), "product": v.get("product"), "n": v.get("n_comparables"),
                          "market": v.get("market"), "breakEven": s0.get("break_even_price"), "markupPrice": s0.get("markup_price"),
                          "unitCost": s0.get("unit_cost"), "atMedian": next((a for a in s0.get("at", []) if a.get("label") == "median"), None),
                          "policy": v.get("policy")}
    elif v:
        j["viability"] = {"verdict": "no_market", "reason": v.get("reason")}
    c = read_json(wd / "comparables.json")
    if c:
        comps = sorted(c.get("comparables", []), key=lambda x: (x.get("shop") or {}).get("sold") or 0, reverse=True)
        j["comparables"] = {"product": c.get("product"), "queries": c.get("queries"), "n": len(c.get("comparables", [])),
                            "rejected": len(c.get("rejected", [])),
                            "top": [{"title": x.get("title"), "url": x.get("url"), "price": x.get("price"), "sold": (x.get("shop") or {}).get("sold"),
                                     "favorers": x.get("num_favorers"), "reason": x.get("reason"), "image": (x.get("images") or [None])[0]} for x in comps[:6]]}
    cand = read_json(wd / "price.candidates.json")
    rej = read_json(wd / "price.rejected.json")
    chosen = read_json(wd / "price.json")
    if cand or rej or chosen:
        src = cand or rej or {}
        j["pricing"] = {"floor": src.get("floor"), "minMarginPct": src.get("min_margin_pct"),
                        "candidates": src.get("candidates", []), "rejectedCandidates": src.get("rejected", []),
                        "proposal": chosen, "rejected": rej.get("reason") if rej else None}
    renders = wd / "renders"
    if renders.exists():
        j["renders"] = {"drafts": [f"/api/jobs/{item}/files/renders/{p.name}" for p in sorted(renders.glob("draft_*.png"))],
                        "hero": f"/api/jobs/{item}/files/renders/hero.png" if (renders / "hero.png").exists() else None,
                        "angles": [f"/api/jobs/{item}/files/renders/{p.name}" for p in sorted(renders.glob("angle_*.png"))],
                        "video": f"/api/jobs/{item}/files/renders/turntable.mp4" if (renders / "turntable.mp4").exists() else None}
    d = read_json(wd / "design.json")
    if d and d.get("ok"):
        url = lambda rel: f"/api/jobs/{item}/files/{rel}"  # noqa: E731
        video = d.get("video") or None
        j["design"] = {"name": d.get("name"), "color": d.get("color"), "reason": d.get("reason"),
                       "photos": [{"id": p["id"], "kind": p["kind"], "url": url(p["file"]), "px": p.get("px")} for p in d.get("photos", [])],
                       "video": {"id": video["id"], "url": url(video["file"]), "seconds": video.get("seconds")} if video else None,
                       "turntable": url(d["videos"]["turntable"]) if d.get("videos", {}).get("turntable") else None,
                       "rejected": d.get("rejected", []), "failed": d.get("failed", []),
                       "spendUsd": (d.get("spend") or {}).get("usd", 0), "warnings": d.get("warnings", [])}
    log = wd / "log.md"
    if log.exists():
        j["log"] = log.read_text()[-6000:]
    return j


@app.get("/jobs")
def list_jobs() -> list[dict]:
    if not WORK.exists():
        return []
    jobs = [j for d in WORK.iterdir() if d.is_dir() and (j := job(d.name))]
    return sorted(jobs, key=lambda j: j.get("createdAt") or "", reverse=True)


@app.get("/jobs/{item}")
def get_job(item: str) -> dict:
    return detail(item)


@app.get("/jobs/{item}/events")
def get_events(item: str) -> list[dict]:
    wd = WORK / item
    if not (wd / "meta.json").exists():
        raise HTTPException(404, "no such job")
    events = [{"stage": "intake", "attempt": 1, "status": "done", "durationMs": 0, "at": (read_json(wd / "meta.json") or {}).get("first_seen")}]
    prev = (wd / "meta.json").stat().st_mtime
    for st in stages():
        produced = [p for pat in st["produces"] for p in wd.glob(pat)]
        if produced:
            t = max(p.stat().st_mtime for p in produced)
            events.append({"stage": "slice" if st["stage"] == "quote" else st["stage"], "attempt": 1, "status": "done",
                           "durationMs": int(max(t - prev, 0) * 1000), "at": iso(t)})
            prev = t
    for name, stage in (("analysis.error.json", "analyze"), ("quote.error.json", "slice"), ("price.rejected.json", "price")):
        p = wd / name
        if p.exists():
            events.append({"stage": stage, "attempt": 1, "status": "failed" if "error" in name else "blocked",
                           "durationMs": 0, "at": iso(p.stat().st_mtime)})
    return sorted(events, key=lambda e: e["at"] or "")


@app.get("/jobs/{item}/files/{path:path}")
def get_file(item: str, path: str) -> FileResponse:
    wd = (WORK / item).resolve()
    target = (wd / path).resolve()
    if wd not in target.parents or not target.is_file() or target.suffix.lower() not in SERVABLE:
        raise HTTPException(404, "no such file")
    return FileResponse(target)


def run_summary(run: str) -> dict | None:
    """A run as its state.json tells it, plus whether this server still has its director."""
    state = read_json(RUNS / run / "state.json")
    if not state:
        return None
    counts = {"queued": 0, "running": 0, "blocked": 0, "done": 0, "failed": 0}
    for item in state.get("items", {}).values():
        for rec in item.get("stages", {}).values():
            counts[rec.get("status", "queued")] = counts.get(rec.get("status", "queued"), 0) + 1
    alive = run_is_live(run, state)
    return {"run": run, "started": state.get("started"), "department": state.get("department"),
            "items": list(state.get("items", {}).keys()), "files": [i.get("file") for i in state.get("items", {}).values()],
            "counts": counts, "cost": state.get("cost", {}), "alive": alive,
            "report": f"/api/runs/{run}/report" if (RUNS / run / "report.md").exists() else None,
            "notes": state.get("notes", [])[-5:]}


@app.get("/runs")
def list_runs(limit: int = 30) -> list[dict]:
    if not RUNS.exists():
        return []
    runs = [r for d in sorted(RUNS.iterdir(), reverse=True) if d.is_dir() and not d.name.startswith("_") and (r := run_summary(d.name))]
    return sorted(runs, key=lambda r: r["started"] or "", reverse=True)[:limit]


@app.get("/runs/unfinished")
def list_unfinished() -> dict:
    items = unfinished_items()
    return {"items": items, "count": len(items)}


@app.post("/runs/resume")
def post_resume() -> dict:
    return resume_all()


@app.get("/runs/{run}/report")
def run_report(run: str):
    p = RUNS / run / "report.md"
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", run) or not p.exists():
        raise HTTPException(404, "no report for that run")
    return FileResponse(p, media_type="text/markdown")


@app.post("/jobs/{item}/approve")
def approve(item: str) -> None:
    raise HTTPException(501, "nothing to approve yet: no publish gate exists until the design route lands")


@app.get("/health")
def health() -> dict:
    with LOCK:
        alive = [r for r, d in DIRECTORS.items() if d["proc"].poll() is None]
    return {"ok": True, "dapi": bool(os.environ.get("DAPI_API_KEY") and os.environ.get("DAPI_API_URL")),
            "etsy": bool(os.environ.get("ETSY_KEYSTRING")), "store": os.environ.get("DAPILOT_STORE_DIR"),
            "work_items": len(list(WORK.iterdir())) if WORK.exists() else 0, "directors_alive": alive}
