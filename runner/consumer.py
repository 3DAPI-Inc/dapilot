#!/usr/bin/env python3
"""The queue consumer: the hosted tier's runner loop, one director invocation per message.

  consumer.py serve                                   poll the SQS queue forever (what doctor.sh --serve runs)
  consumer.py once --message FILE [--local-bucket DIR --local-backend DIR] [--director-args "..."]
                                                      handle one message from a JSON file, for a laptop test

What it does, per message (docs/hosting.md, "The consumer"):

  receive -> heartbeat the visibility timeout -> pull the seller's prefix from the bucket to a local
  root -> write the store's env file and limits from the backend's per-seller values -> run the
  plugin's director on that root -> post one event per director log line -> read the outcome from
  the ledger and the markers -> push the root back (secrets excluded) -> delete the message.

Messages are ids and keys only, never files:

  {"kind": "run",     "seller": "<id>", "run": "<id>", "keys": ["intake/<seller>/<run>/<file>", ...]}
  {"kind": "approve", "seller": "<id>", "run": "<id>", "item": "<hash>", "price": 12.99, "by": "<name>"}
  {"kind": "retry",   "seller": "<id>", "run": "<id>", "item": "<hash>", "stage": "<stage>",
                      "reason": "<optional: why it could end differently>", "by": "<optional: who asked>"}

A retry takes one stopped stage up again, failed or blocked alike (the dashboard's Try again and Continue,
and the supervisor when it exists): `ledger.py retry` clears the marker and queues the stage in the same
run with fresh attempts, the marker is deleted from the bucket as well (push never deletes, so a marker
only cleared locally came back on the next pull, seen 2026-10-21), and the director continues from there.

The plugin is never imported, patched or reached into. It sees a directory and a command: the root
at $DAPILOT_DATA_DIR/<seller> (store/, work/, runs/, intake/), DAPILOT_STORE_DIR and
DAPILOT_PROJECT_DIR pointing at it, and run_intake.py, approve_price.py or ledger.py as the plugin ships
them. Anything the runner needs the plugin to do differently is a plugin feature, not a line here.

The bucket and the backend are each behind one small class with an S3 or HTTP form and a local
directory form, so the whole loop runs on a laptop with no AWS account: `once --local-bucket DIR
--local-backend DIR`. The local backend reads DIR/credentials.json and DIR/limits.json and appends
events to DIR/events.jsonl and the outcome to DIR/outcome.json.

Environment (the task definition sets these; nothing per seller is in them):
  DAPILOT_QUEUE_URL, DAPILOT_BUCKET, DAPILOT_BACKEND_URL, DAPILOT_BACKEND_TOKEN
  DAPILOT_DATA_DIR (default /data/dapilot), DAPILOT_PLUGIN_DIR, DAPILOT_PYTHON, CHROME
  ANTHROPIC_API_KEY   bills the workers; written into every seller's env file for the run
  AWS_REGION          the queue's and bucket's region (AWS_DEFAULT_REGION is read too); credentials come
                      from AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, the runner's own IAM user
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DAPILOT_DATA_DIR") or "/data/dapilot")
PLUGIN_DIR = Path(os.environ.get("DAPILOT_PLUGIN_DIR") or HERE.parent / "plugins" / "dapilot")
PYTHON = os.environ.get("DAPILOT_PYTHON") or sys.executable
def aws_region() -> str:
    """The region for every AWS client. botocore reads only AWS_DEFAULT_REGION on its own; Coolify
    resources and the README name AWS_REGION, so both are accepted and the error names them."""
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise SystemExit("AWS_REGION is not set (us-east-1 for the dapilot queue and bucket)")
    return region


HEARTBEAT_S = 60          # how often the message's visibility is extended while a job runs
VISIBILITY_S = 300        # by how much; a dead container stops extending and the message reappears
WAIT_S = 20               # SQS long poll
# What never leaves the container: the store's secrets, the plugin's locks, a director's liveness file, and
# the intake copy of an upload (the bucket already holds it under intake/, and the work dir holds source.*).
NEVER_SYNCED = ("store/.env", "store/etsy_token", "store/social/", "/.lock", "director.alive", ".sync.json", "intake/")

# The director's own log lines, exactly as plugins/dapilot/scripts/watch_run.py reads them. Each one
# becomes an event; the dashboard shows events, not logs.
START = re.compile(r"^\[\d\d:\d\d:\d\d\] run (?P<run>\S+): (?P<items>\d+) items")
DISPATCH = re.compile(r"^\[\d\d:\d\d:\d\d\] pass (?P<pass>\d+): dispatching (?P<n>\d+) worker\(s\): (?P<list>.*)$")
RESULT = re.compile(r"^\[\d\d:\d\d:\d\d\] (?P<hash>[0-9a-f]{8}) (?P<stage>\w+)#(?P<attempt>\d+) -> (?P<status>\w+)"
                    r"(?: \| (?P<secs>[\d.]+)s, (?P<turns>\d+) turns, (?P<usd>[\d.]+) USD)?(?: \| (?P<summary>.*))?$")
DONE = re.compile(r"^\[\d\d:\d\d:\d\d\] done: (?P<counts>\{.*?\}), (?P<workers>\d+) workers, (?P<usd>[\d.]+) USD, (?P<secs>[\d.]+)s")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] runner: {msg}", file=sys.stderr, flush=True)


def excluded(rel: str) -> bool:
    return any(p in f"/{rel}" or rel.startswith(p) for p in NEVER_SYNCED)


# ------------------------------------------------------------------ the bucket

class Bucket:
    """The seller stores between runs. S3 in the task; a directory for a laptop test. Sync is by
    size and name: good enough for a root that only the director writes, at job boundaries."""

    def __init__(self, name: str | None, local: Path | None):
        self.local = local
        self.name = name
        self.s3 = None
        if local is None:
            import boto3  # only the task needs it; a laptop test does not
            self.s3 = boto3.client("s3", region_name=aws_region())

    # -- listing
    def _remote(self, prefix: str) -> dict[str, int]:
        if self.local is not None:
            base = self.local / prefix
            return {str(p.relative_to(base)): p.stat().st_size for p in base.rglob("*") if p.is_file()} if base.exists() else {}
        out: dict[str, int] = {}
        token = None
        while True:
            kw = {"Bucket": self.name, "Prefix": prefix.rstrip("/") + "/"}
            if token:
                kw["ContinuationToken"] = token
            res = self.s3.list_objects_v2(**kw)
            for o in res.get("Contents", []):
                out[o["Key"][len(kw["Prefix"]):]] = o["Size"]
            token = res.get("NextContinuationToken")
            if not token:
                return out

    def get(self, key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self.local is not None:
            shutil.copyfile(self.local / key, dest)
        else:
            self.s3.download_file(self.name, key, str(dest))

    def put(self, src: Path, key: str) -> None:
        if self.local is not None:
            dst = self.local / key
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        else:
            self.s3.upload_file(str(src), self.name, key)

    def delete(self, key: str) -> None:
        """One object, gone for good. `push` never deletes, on purpose: the root is a seller's store and
        a missing local file is more often a failed pull than a decision. The one thing that is decided
        away is a stage marker a retry cleared, and that is the only caller."""
        if self.local is not None:
            (self.local / key).unlink(missing_ok=True)
        else:
            self.s3.delete_object(Bucket=self.name, Key=key)

    def pull(self, prefix: str, root: Path) -> int:
        """Bring the seller's root down: files missing locally or of another size. Returns the count."""
        n = 0
        for rel, size in self._remote(prefix).items():
            if excluded(rel):
                continue
            dest = root / rel
            if dest.exists() and dest.stat().st_size == size:
                continue
            self.get(f"{prefix.rstrip('/')}/{rel}", dest)
            n += 1
        return n

    def push(self, root: Path, prefix: str) -> int:
        """Send the root back: files new or of another size since the last listing. Secrets never go."""
        remote = self._remote(prefix)
        n = 0
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rel = str(p.relative_to(root))
            if excluded(rel) or remote.get(rel) == p.stat().st_size:
                continue
            self.put(p, f"{prefix.rstrip('/')}/{rel}")
            n += 1
        return n


# ------------------------------------------------------------------ the backend

class Backend:
    """Per-seller values in, events and outcomes out. HTTP in the task; files for a laptop test."""

    def __init__(self, url: str | None, token: str | None, local: Path | None):
        self.url, self.token, self.local = (url or "").rstrip("/"), token, local

    def _http(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(self.url + path, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"content-type": "application/json", "authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode()
            return json.loads(text) if text.strip() else {}

    def credentials(self, seller: str) -> dict[str, str]:
        """The store's env values for this seller: DAPI_API_KEY, DAPI_API_URL, ETSY_*, FAL_KEY; etsy_token when connected."""
        if self.local is not None:
            return json.loads((self.local / "credentials.json").read_text())
        return self._http("GET", f"/internal/dapilot/sellers/{seller}/credentials")

    def limits(self, seller: str) -> dict:
        """What the plan allows, as config.json keys: budget.usd_per_run, max_workers_per_lead, max_attempts."""
        if self.local is not None:
            p = self.local / "limits.json"
            return json.loads(p.read_text()) if p.exists() else {}
        return self._http("GET", f"/internal/dapilot/sellers/{seller}/limits")

    def event(self, seller: str, run: str, kind: str, **data) -> None:
        ev = {"at": now(), "seller": seller, "run": run, "kind": kind, **data}
        if self.local is not None:
            with (self.local / "events.jsonl").open("a") as f:
                f.write(json.dumps(ev) + "\n")
            return
        try:
            self._http("POST", f"/internal/dapilot/runs/{run}/events", ev)
        except (urllib.error.URLError, OSError) as e:   # an event is telemetry; the run must not die for it
            log(f"event not delivered: {e}")

    def outcome(self, seller: str, run: str, doc: dict) -> None:
        if self.local is not None:
            (self.local / "outcome.json").write_text(json.dumps(doc, indent=2))
            return
        self._http("POST", f"/internal/dapilot/runs/{run}/outcome", doc)


# ------------------------------------------------------------------ the seller's root

def seller_root(seller: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", seller):
        raise ValueError(f"seller id {seller!r} is not a path segment")
    return DATA_DIR / seller


def seed_store(store: Path) -> None:
    """A new seller store gets the plugin's templates, as quickstart seeds a laptop's. Nothing present is touched."""
    (store / "reference").mkdir(parents=True, exist_ok=True)
    t = PLUGIN_DIR / "templates"
    for src, dst in (("config.example.json", "config.json"), ("pricing-policy.example.json", "reference/pricing-policy.json")):
        if (t / src).exists() and not (store / dst).exists():
            shutil.copyfile(t / src, store / dst)


def write_env(store: Path, values: dict[str, str]) -> None:
    """The store's env file, written whole for this job and never synced. Mode 600; the plugin's guard
    hook keeps the workers' tools away from it."""
    lines = ["# written by the dapilot runner for this job; not synced, not edited by hand"]
    lines += [f"{k}={v}" for k, v in values.items() if v is not None and v != ""]
    p = store / ".env"
    p.write_text("\n".join(lines) + "\n")
    p.chmod(0o600)


def template_budget() -> dict:
    """The plugin release's own `budget` block, from its config template. A seller's config.json is seeded
    once and then persisted, so a stage budget the template raised later never reached an existing seller:
    the design worker's 1.50 USD of 2026-10-23 was the template's old value, frozen in S3. Written under the
    plan limits on every job, so the release's figures apply and the plan's still win."""
    t = PLUGIN_DIR / "templates" / "config.example.json"
    try:
        return {"budget": json.loads(t.read_text()).get("budget") or {}}
    except (OSError, ValueError):
        return {}


def write_limits(store: Path, limits: dict) -> None:
    """Plan limits into config.json, top-level keys and `budget` merged; the plugin reads them as its own.
    Nested dicts merge one level down (`budget.usd_per_stage` keeps the keys a layer does not name)."""
    if not limits:
        return
    p = store / "config.json"
    cfg = json.loads(p.read_text()) if p.exists() else {}

    def merge(into: dict, src: dict) -> None:
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(into.get(k), dict):
                merge(into[k], v)
            else:
                into[k] = v

    merge(cfg, limits)
    p.write_text(json.dumps(cfg, indent=1))


def prepare(seller: str, backend: Backend, bucket: Bucket) -> Path:
    root = seller_root(seller)
    root.mkdir(parents=True, exist_ok=True)
    pulled = bucket.pull(f"sellers/{seller}", root)
    seed_store(root / "store")
    creds = backend.credentials(seller)
    token = creds.pop("etsy_token", None)
    if token:
        p = root / "store" / "etsy_token.json"
        p.write_text(json.dumps(token) if isinstance(token, dict) else str(token))
        p.chmod(0o600)
    env = {**creds, "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""), "DAPILOT_BILLING": "api",
           "DAPILOT_PYTHON": PYTHON, "CHROME": os.environ.get("CHROME", "")}
    write_env(root / "store", env)
    limits = template_budget()
    for k, v in backend.limits(seller).items():
        if isinstance(v, dict) and isinstance(limits.get(k), dict):
            limits[k] = {**limits[k], **v}
        else:
            limits[k] = v
    write_limits(root / "store", limits)
    log(f"{seller}: root ready at {root} ({pulled} files pulled)")
    return root


def plugin_env(root: Path) -> dict[str, str]:
    return {**os.environ, "DAPILOT_PROJECT_DIR": str(root), "DAPILOT_STORE_DIR": str(root / "store"),
            "CLAUDE_PLUGIN_ROOT": str(PLUGIN_DIR), "DAPILOT_PYTHON": PYTHON, "DAPILOT_BILLING": "api"}


# ------------------------------------------------------------------ the director

ALIVE_S = 30              # run_intake.py rewrites runs/<id>/director.alive every 5 s; older than this it is dead
DIRECTOR_WAIT_S = 3600    # how long a job waits for a live director on the same root before starting its own


def live_director(root: Path) -> dict | None:
    """The run whose director is alive on this root now, or None: the file approve_price.py and the plugin's
    monitor trust, aged the same way (a crash leaves it to age out). One root, one director: the plugin's
    ledger and the S3 sync both assume a single writer, so a second one here is never started beside it."""
    runs = root / "runs"
    if not runs.is_dir():
        return None
    now_t = time.time()
    for rd in runs.iterdir():
        alive = rd / "director.alive"
        try:
            age = now_t - alive.stat().st_mtime
        except OSError:
            continue
        if age <= ALIVE_S:
            return {"run": rd.name, "age_s": int(age)}
    return None


def wait_for_director(root: Path, stop: threading.Event, label: str) -> bool:
    """Blocks while a director lives on the root. The message's visibility is kept by the heartbeat
    meanwhile. True when the root is free; False when the wait ran out or the process was told to stop."""
    waited = 0
    while (live := live_director(root)) is not None:
        if waited == 0:
            log(f"{label}: director of run {live['run']} is alive on this root; waiting for it to end")
        if stop.is_set() or waited >= DIRECTOR_WAIT_S:
            return False
        stop.wait(5)
        waited += 5
    return True


class Director:
    """One run_intake.py process: started, tailed into events, stopped on SIGTERM."""

    def __init__(self, root: Path, seller: str, run: str, backend: Backend):
        self.root, self.seller, self.run, self.backend = root, seller, run, backend
        self.proc: subprocess.Popen | None = None

    def start(self, args: list[str]) -> None:
        if (live := live_director(self.root)) is not None:
            raise RuntimeError(f"a director is alive on this root (run {live['run']}); not starting a second")
        cmd = [PYTHON, str(PLUGIN_DIR / "scripts" / "run_intake.py"), *args, "--billing", "api"]
        log(f"{self.seller}/{self.run}: {' '.join(cmd[2:])}")
        self.proc = subprocess.Popen(cmd, cwd=self.root, env=plugin_env(self.root), text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)

    def tail(self) -> dict:
        """Reads stderr line by line until exit, posting each recognised line; returns the stdout summary."""
        assert self.proc is not None and self.proc.stderr is not None
        for line in self.proc.stderr:
            line = line.rstrip("\n")
            self._event(line)
        out = self.proc.stdout.read() if self.proc.stdout else ""
        self.proc.wait()
        summary = next((json.loads(c) for c in _json_candidates(out) if c), {})
        summary["exit"] = self.proc.returncode
        return summary

    def _event(self, line: str) -> None:
        b, s, r = self.backend, self.seller, self.run
        if m := START.match(line):
            b.event(s, r, "run.started", items=int(m["items"]))
        elif m := DISPATCH.match(line):
            b.event(s, r, "pass.dispatched", **{"pass": int(m["pass"]), "workers": int(m["n"]), "briefs": m["list"]})
        elif m := RESULT.match(line):
            b.event(s, r, "stage.result", item8=m["hash"], stage=m["stage"], attempt=int(m["attempt"]), status=m["status"],
                    seconds=float(m["secs"]) if m["secs"] else None, turns=int(m["turns"]) if m["turns"] else None,
                    usd=float(m["usd"]) if m["usd"] else None, summary=m["summary"])
        elif m := DONE.match(line):
            b.event(s, r, "run.ended", counts=m["counts"], workers=int(m["workers"]), usd=float(m["usd"]), seconds=float(m["secs"]))
        else:
            b.event(s, r, "log", line=line[-500:])

    def stop(self) -> None:
        """SIGTERM to the director's group, as a deploy or a Spot reclaim gives us two minutes."""
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                self.proc.wait(timeout=90)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)


def _json_candidates(text: str):
    """The director prints one JSON document on stdout, pretty-printed; find it from the end."""
    s = text.strip()
    if s.startswith("{") and s.endswith("}"):
        yield s
    for line in reversed(s.splitlines()):
        if line.startswith("{") and line.endswith("}"):
            yield line
    yield ""


def outcome(root: Path, run: str) -> dict:
    """What the ledger and the markers say, as one document for the dashboard: per item per stage,
    the open gates with their candidates, and anything blocked or failed with its reason."""
    state_p = root / "runs" / run / "state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {}
    items = []
    for h, it in (state.get("items") or {}).items():
        wd = root / it["workdir"]
        rec = {"item": h, "file": it.get("file"), "stages": it.get("stages", {}), "gate": None, "markers": []}
        if (wd / "price.json").exists() and not (wd / "price.approved.json").exists():
            price = json.loads((wd / "price.json").read_text() or "{}")
            cands = (wd / "price.candidates.json")
            rec["gate"] = {"proposal": price, "candidates": json.loads(cands.read_text()) if cands.exists() else None}
        for m in sorted(wd.glob("*.failed.json")) + sorted(wd.glob("*.blocked.json")):
            doc = json.loads(m.read_text() or "{}")
            rec["markers"].append({"file": m.name, **{k: doc.get(k) for k in ("stage", "reason", "question", "run", "attempt", "worker_log")}})
        items.append(rec)
    counts: dict[str, int] = {}
    for rec in items:                                   # the ledger keeps status per stage; the dashboard wants totals
        for st in rec["stages"].values():
            counts[st.get("status", "queued")] = counts.get(st.get("status", "queued"), 0) + 1
    return {"run": run, "at": now(), "counts": counts, "cost": state.get("cost"),
            "gates_open": sum(1 for r in items if r["gate"]), "notes": (state.get("notes") or [])[-20:], "items": items}


# ------------------------------------------------------------------ the handlers

def handle(body: dict, backend: Backend, bucket: Bucket, extra_args: list[str], stop: threading.Event) -> dict:
    """One message, handled once. The handlers are idempotent against the queue's at-least-once delivery
    and a person's repeated clicks (both seen on 2026-10-23): an approval already on disk starts nothing,
    a retry the ledger refuses starts nothing, and no director is started while one lives on the root."""
    seller, run, kind = body["seller"], body["run"], body["kind"]
    root = prepare(seller, backend, bucket)
    if not wait_for_director(root, stop, f"{seller}/{run}"):
        raise RuntimeError("a director stayed alive on this root for the whole wait; message left for redelivery")
    d = Director(root, seller, run, backend)
    if kind == "run":
        intake = root / "intake" / run
        intake.mkdir(parents=True, exist_ok=True)
        for key in body.get("keys") or []:
            bucket.get(key, intake / Path(key).name)
        d.start([str(intake), "--run", run, *extra_args])
    elif kind == "approve":
        wd = root / "work" / body["item"]
        cmd = [PYTHON, str(PLUGIN_DIR / "scripts" / "approve_price.py"), "approve", "--workdir", str(wd), "--no-start",
               *(["--price", str(body["price"])] if body.get("price") is not None else []),
               *(["--by", str(body["by"])] if body.get("by") else [])]
        res = subprocess.run(cmd, cwd=root, env=plugin_env(root), capture_output=True, text=True)
        verdict = next((json.loads(c) for c in _json_candidates(res.stdout) if c), {"ok": False, "reason": res.stderr[-300:]})
        backend.event(seller, run, "gate.answered", item=body["item"], ok=bool(verdict.get("ok")), detail=verdict)
        if res.returncode != 0 or not verdict.get("ok"):
            return {"ok": False, "reason": f"approval refused: {verdict.get('reason')}", "outcome": outcome(root, run)}
        if verdict.get("cached"):
            # The price was approved by an earlier message, whose director took the item on from there.
            # This one is a repeat (a second click, a redelivery): nothing to start. The outcome goes up so
            # the dashboard shows the item as it is, and the message is done.
            log(f"{seller}/{run}: {body['item'][:8]} already approved at {verdict.get('at')} by {verdict.get('by')}; nothing started")
            doc = outcome(root, run)
            doc["director"] = {"exit": 0, "skipped": "already approved"}
            backend.outcome(seller, run, doc)
            return {"ok": True, "summary": doc["director"], "outcome": doc}
        d.start(["--items", body["item"], "--run", run, *extra_args])
    elif kind == "retry":
        item, stage = body["item"], body["stage"]
        cmd = [PYTHON, str(PLUGIN_DIR / "scripts" / "ledger.py"), "retry", "--run", run, "--item", item, "--stage", stage,
               *(["--reason", str(body["reason"])] if body.get("reason") else []),
               *(["--by", str(body["by"])] if body.get("by") else [])]
        res = subprocess.run(cmd, cwd=root, env=plugin_env(root), capture_output=True, text=True)
        verdict = next((json.loads(c) for c in _json_candidates(res.stdout) if c), {"ok": False, "reason": res.stderr[-300:]})
        # The ledger cleared the marker on disk; the bucket still has it, and push never deletes.
        for name in verdict.get("cleared") or []:
            bucket.delete(f"sellers/{seller}/work/{item}/{name}")
        backend.event(seller, run, "stage.retried", item=item, stage=stage, ok=bool(verdict.get("ok")), detail=verdict)
        if res.returncode != 0 or not verdict.get("ok"):
            return {"ok": False, "reason": f"retry refused: {verdict.get('reason')}", "outcome": outcome(root, run)}
        d.start(["--items", item, "--run", run, *extra_args])
    else:
        return {"ok": False, "reason": f"unknown message kind {kind!r}"}

    watcher = threading.Thread(target=lambda: (stop.wait(), d.stop()), daemon=True)
    watcher.start()
    summary = d.tail()
    doc = outcome(root, run)
    doc["director"] = summary
    backend.outcome(seller, run, doc)
    pushed = bucket.push(root, f"sellers/{seller}")
    log(f"{seller}/{run}: director exit {summary.get('exit')}, {pushed} files pushed")
    return {"ok": summary.get("exit") in (0, 1) and not stop.is_set(), "summary": summary, "outcome": doc}


# ------------------------------------------------------------------ the loop

def stop_on_signal() -> threading.Event:
    """One event for every loop in this process, set by SIGTERM or SIGINT. Called from the main thread,
    the only place Python lets a handler be installed."""
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    return stop


def poll(queue: str, handler, label: str = "job", stop: threading.Event | None = None) -> int:
    """The receive, heartbeat, handle, delete loop, shared with the supervisor (supervisor.py): one message
    at a time, its visibility extended while the handler runs, deleted when the handler returns (done or
    final-for-a-person, both are final), left for redelivery when the handler raises (three of those send
    it to the dead-letter queue), and left unacknowledged on SIGTERM so the next process takes it. A caller
    running several loops in one process passes the shared `stop`; alone, the loop installs its own."""
    import boto3
    sqs = boto3.client("sqs", region_name=aws_region())
    if stop is None:
        stop = stop_on_signal()
    log(f"polling {queue}")
    while not stop.is_set():
        res = sqs.receive_message(QueueUrl=queue, MaxNumberOfMessages=1, WaitTimeSeconds=WAIT_S,
                                  VisibilityTimeout=VISIBILITY_S)
        for msg in res.get("Messages", []):
            handle_id = msg["ReceiptHandle"]
            beat = threading.Event()

            def heartbeat():
                while not beat.wait(HEARTBEAT_S):
                    try:
                        sqs.change_message_visibility(QueueUrl=queue, ReceiptHandle=handle_id, VisibilityTimeout=VISIBILITY_S)
                    except Exception as e:  # noqa: BLE001  the job goes on; the message may reappear, which the ledger tolerates
                        log(f"heartbeat failed: {e}")

            threading.Thread(target=heartbeat, daemon=True).start()
            try:
                body = json.loads(msg["Body"])
                result = handler(body, stop)
                if stop.is_set():
                    log("stopping with the message unacknowledged; it reappears for the next task")
                    return 0
                if not result.get("ok"):
                    log(f"{label} ended not ok: {result.get('reason') or result.get('summary', {}).get('exit')}")
                sqs.delete_message(QueueUrl=queue, ReceiptHandle=handle_id)
            except Exception as e:  # noqa: BLE001
                log(f"{label} crashed, message left for redelivery: {e!r}")
            finally:
                beat.set()
    return 0


def serve(backend: Backend, bucket: Bucket, extra_args: list[str]) -> int:
    """The runs queue in this thread and, when DAPILOT_SUPERVISE_QUEUE_URL is set, the supervisor's loop
    in a second one: the same container, the same credentials, one SIGTERM for both. The supervisor is
    read-only on the store and talks only to the backend, so it has nothing to share with a job beyond
    the process. Without the variable the runner behaves as before and nothing supervises."""
    stop = stop_on_signal()
    supervise_url = os.environ.get("DAPILOT_SUPERVISE_QUEUE_URL")
    if supervise_url:
        from supervisor import SelfQueue, SupervisorBackend, handle as supervise   # same directory, imported late: supervisor imports this module
        sup_backend = SupervisorBackend(backend.url, backend.token, backend.local)
        sup_queue = SelfQueue(supervise_url, None)
        threading.Thread(target=poll, args=(supervise_url, lambda body, _stop: supervise(body, sup_backend, sup_queue), "decision", stop),
                         name="supervisor", daemon=True).start()
        log("supervising in this container as well")
    return poll(os.environ["DAPILOT_QUEUE_URL"],
                lambda body, stop_: handle(body, backend, bucket, extra_args, stop_), "job", stop)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="poll the queue forever")
    one = sub.add_parser("once", help="handle one message from a file")
    one.add_argument("--message", required=True)
    for p in (one,):
        p.add_argument("--local-bucket", help="a directory standing in for the S3 bucket")
        p.add_argument("--local-backend", help="a directory standing in for the backend")
        p.add_argument("--director-args", default="", help="extra run_intake.py arguments, e.g. --dry-run")
    a = ap.parse_args()
    if not (PLUGIN_DIR / "scripts" / "run_intake.py").exists():
        print(json.dumps({"ok": False, "reason": f"no plugin at {PLUGIN_DIR}; set DAPILOT_PLUGIN_DIR"})); return 2
    local_b = Path(a.local_bucket).resolve() if getattr(a, "local_bucket", None) else None
    local_k = Path(a.local_backend).resolve() if getattr(a, "local_backend", None) else None
    bucket = Bucket(os.environ.get("DAPILOT_BUCKET"), local_b)
    backend = Backend(os.environ.get("DAPILOT_BACKEND_URL"), os.environ.get("DAPILOT_BACKEND_TOKEN"), local_k)
    extra = [x for x in getattr(a, "director_args", "").split() if x]
    if a.cmd == "serve":
        return serve(backend, bucket, extra)
    body = json.loads(Path(a.message).read_text())
    result = handle(body, backend, bucket, extra, threading.Event())
    print(json.dumps({k: v for k, v in result.items() if k != "outcome"}, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
