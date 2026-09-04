"""dapilot job API: the frontend's backend. Holds the dapi key so the browser never does.

  .venv/bin/uvicorn server.app:app --port 3100 --reload

POST /jobs (multipart `files`)   store each file under work/<sha256>/, then analyse and quote it
GET  /jobs                        every item known, newest first, stage and status derived from files
GET  /jobs/{id}                   one item
GET  /jobs/{id}/events            what ran, from work/<id>/events.json
POST /jobs/{id}/approve           not available until a publish gate exists (501)

Stage and status are never stored; they are read off the work dir the same
way ledger.py does, so the UI and the pipeline can never disagree:
  quote.json        -> price done          quote.error.json -> slice failed
  quote.pending.json or a running thread -> slice running
  analysis.json     -> slice queued        nothing yet -> analyze running/queued
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import JSONResponse

ROOT = Path(os.environ.get("DAPILOT_PROJECT_DIR") or Path(__file__).resolve().parents[1])
PLUGIN = ROOT / "plugins" / "dapilot"
SCRIPTS = PLUGIN / "scripts"
WORK = ROOT / "work"
SUPPORTED = {".stl", ".3mf", ".obj", ".step", ".stp"}


def load_env() -> None:
    env = Path(os.environ.get("DAPILOT_STORE_DIR") or ROOT / "store") / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
PY = os.environ.get("DAPILOT_PYTHON") or sys.executable
app = FastAPI(title="dapilot job API")
RUNNING: dict[str, str] = {}          # item -> stage currently executing in a thread
LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def event(wd: Path, stage: str, status: str, t0: float, attempt: int = 1) -> None:
    p = wd / "events.json"
    events = json.loads(p.read_text()) if p.exists() else []
    events.append({"stage": stage, "attempt": attempt, "status": status,
                   "durationMs": int((time.time() - t0) * 1000), "at": now()})
    p.write_text(json.dumps(events, indent=2))


def run_pipeline(item: str) -> None:
    """analyze then quote, in a thread. Same scripts the agents use; no model here."""
    wd = WORK / item
    try:
        if not (wd / "analysis.json").exists():
            with LOCK:
                RUNNING[item] = "analyze"
            t0 = time.time()
            src = next(p for p in wd.iterdir() if p.stem == "source")
            r = subprocess.run([PY, str(SCRIPTS / "analyze_mesh.py"), str(src)], capture_output=True, text=True)
            if r.returncode == 0:
                (wd / "analysis.json").write_text(r.stdout)
                event(wd, "analyze", "done", t0)
            else:
                (wd / "analysis.error.json").write_text(r.stdout or json.dumps({"reason": r.stderr[-500:]}))
                event(wd, "analyze", "failed", t0)
                return
        if not (wd / "quote.json").exists() and not (wd / "quote.error.json").exists():
            with LOCK:
                RUNNING[item] = "slice"
            t0 = time.time()
            r = subprocess.run([PY, str(SCRIPTS / "quote.py"), "--workdir", str(wd), "--max-wait", "540"],
                               capture_output=True, text=True)
            event(wd, "slice", "done" if r.returncode == 0 else ("running" if r.returncode == 3 else "failed"), t0)
    finally:
        with LOCK:
            RUNNING.pop(item, None)


def job(item: str) -> dict | None:
    wd = WORK / item
    meta_p = wd / "meta.json"
    if not meta_p.exists():
        return None
    meta = json.loads(meta_p.read_text())
    j = {"id": item, "fileName": meta.get("file", item), "createdAt": meta.get("first_seen"), "updatedAt": meta.get("first_seen")}
    running = RUNNING.get(item)
    if (wd / "quote.json").exists():
        q = json.loads((wd / "quote.json").read_text())
        j.update(stage="price", status="done", updatedAt=q.get("fetched_at"), price=q.get("unit_price"),
                 quoteId=q.get("publicId"), batch=q.get("batch"))
    elif (wd / "quote.error.json").exists():
        q = json.loads((wd / "quote.error.json").read_text())
        j.update(stage="slice", status="failed", updatedAt=q.get("fetched_at"), quoteId=q.get("publicId"),
                 reason=q.get("errorMessage") or q.get("status") or "slicer error")
    elif running == "slice" or (wd / "quote.pending.json").exists():
        j.update(stage="slice", status="running", reason="slicing on dapi")
    elif (wd / "analysis.error.json").exists():
        a = json.loads((wd / "analysis.error.json").read_text())
        j.update(stage="analyze", status="failed", reason=a.get("reason", "analysis failed"))
    elif (wd / "analysis.json").exists():
        j.update(stage="slice", status="queued")
    elif running == "analyze":
        j.update(stage="analyze", status="running")
    else:
        j.update(stage="intake", status="queued")
    a_p = wd / "analysis.json"
    if a_p.exists():
        a = json.loads(a_p.read_text())
        j["analysis"] = {k: a.get(k) for k in ("bbox_mm", "volume_mm3", "bodies", "watertight_bodies", "triangles")}
    return j


@app.post("/jobs")
async def create_jobs(files: list[UploadFile]) -> JSONResponse:
    created = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in SUPPORTED:
            raise HTTPException(400, f"unsupported: {ext or 'no extension'}")
        data = await f.read()
        if not data:
            raise HTTPException(400, f"{f.filename}: empty file")
        item = hashlib.sha256(data).hexdigest()
        wd = WORK / item
        wd.mkdir(parents=True, exist_ok=True)
        src = wd / f"source{ext}"
        if not src.exists():
            src.write_bytes(data)
        if not (wd / "meta.json").exists():
            (wd / "meta.json").write_text(json.dumps({"file": f.filename, "bytes": len(data), "sha256": item,
                                                     "first_seen": now(), "via": "frontend"}, indent=2))
        if item not in RUNNING:
            threading.Thread(target=run_pipeline, args=(item,), daemon=True).start()
        created.append(job(item))
    return JSONResponse(created, status_code=201)


@app.get("/jobs")
def list_jobs() -> list[dict]:
    if not WORK.exists():
        return []
    jobs = [j for d in WORK.iterdir() if d.is_dir() and (j := job(d.name))]
    return sorted(jobs, key=lambda j: j.get("createdAt") or "", reverse=True)


@app.get("/jobs/{item}")
def get_job(item: str) -> dict:
    j = job(item)
    if not j:
        raise HTTPException(404, "no such job")
    return j


@app.get("/jobs/{item}/events")
def get_events(item: str) -> list[dict]:
    p = WORK / item / "events.json"
    if not (WORK / item / "meta.json").exists():
        raise HTTPException(404, "no such job")
    return json.loads(p.read_text()) if p.exists() else []


@app.post("/jobs/{item}/approve")
def approve(item: str) -> None:
    raise HTTPException(501, "nothing to approve yet: no publish gate exists until the design route lands")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "dapi": bool(os.environ.get("DAPI_API_KEY") and os.environ.get("DAPI_API_URL")),
            "work_items": len(list(WORK.iterdir())) if WORK.exists() else 0, "running": dict(RUNNING)}
