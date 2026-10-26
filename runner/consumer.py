#!/usr/bin/env python3
"""The queue consumer: the hosted tier's runner loop, one director invocation per message.

  consumer.py serve                                   poll the SQS queue forever (what doctor.sh --serve runs)
  consumer.py once --message FILE [--local-bucket DIR --local-backend DIR] [--director-args "..."]
                                                      handle one message from a JSON file, for a laptop test

What it does, per message (docs/hosting.md, "The consumer"):

  receive -> heartbeat the visibility timeout -> pull the seller's prefix from the bucket to a local
  root -> write the store's env file and limits from the backend's per-seller values -> run the
  plugin's director on that root -> forward each line of the director's events.jsonl as an event -> read the outcome from
  the ledger and the markers -> push the root back (secrets excluded) -> delete the message.

Messages are ids and keys only, never files:

  {"kind": "run",     "seller": "<id>", "run": "<id>", "keys": ["intake/<seller>/<run>/<file>", ...],
                      "description": "<optional: what the seller wrote about the upload; to the director as --description>"}
  {"kind": "approve", "seller": "<id>", "run": "<id>", "item": "<hash>", "price": 12.99, "by": "<name>"}
  {"kind": "retry",   "seller": "<id>", "run": "<id>", "item": "<hash>", "stage": "<stage>",
                      "reason": "<optional: why it could end differently>", "by": "<optional: who asked>"}

A retry takes one stopped stage up again, failed or blocked alike (the dashboard's Try again and Continue,
and the supervisor): `ledger.py retry` clears the marker and queues the stage in the same run with fresh
attempts, and the director continues from there. A done stage with a `<stage>.review.json` marker (a
check found something in its output, 2026-10-23) is redone the same way: the ledger sets its outputs
aside and `push` deletes from the bucket what the root no longer holds, within the manifest rule below.

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
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
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


def _schema_version() -> str:
    try:
        return (PLUGIN_DIR / "schema" / "VERSION").read_text().strip()
    except OSError:
        return "1.0"


SCHEMA_VERSION = _schema_version()   # the plugin's contract version (plugins/dapilot/schema/VERSION)
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
MANIFEST = ".sync.json"   # at the root: every path the last sync saw on both sides, the only paths push may delete
# What never leaves the container: the store's secrets, the plugin's locks, a director's liveness file, and
# the intake copy of an upload (the bucket already holds it under intake/, and the work dir holds source.*).
NEVER_SYNCED = ("store/.env", "store/etsy_token", "store/social/", "/.lock", "director.alive", ".sync.json", "intake/")

# What the director says to machines: runs/<run>/events.jsonl, one JSON document per line, each checked
# against the plugin's schema/event.schema.json before it was written (2026-10-25). The runner follows
# the file while the director runs and posts every line to the backend with `seller` added. The stderr
# log is for people and goes into this process's own log; nothing is parsed out of it any more.
EVENTS_FILE = "events.jsonl"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] runner: {msg}", file=sys.stderr, flush=True)


def excluded(rel: str) -> bool:
    return any(p in f"/{rel}" or rel.startswith(p) for p in NEVER_SYNCED)


# ------------------------------------------------------------------ the bucket

def md5_of(p: Path) -> str:
    h = hashlib.md5()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Bucket:
    """The seller stores between runs. S3 in the task; a directory for a laptop test. Sync is by name,
    size and content (2026-10-23): a file of the same size on both sides is compared by its MD5 against
    the object's ETag (S3's ETag is the MD5 for an object uploaded in one part; a multipart ETag carries
    a dash and only the size is compared). Before that a same-sized rewrite, a state.json with one
    status flipped, was neither pulled nor pushed.

    Push deletes, within one rule: only a path the last sync saw on both sides (the root's .sync.json
    manifest) and that is gone from the root now. The plugin removes files on purpose (a marker a retry
    cleared, outputs a redo set aside under .redo/), and those used to come back from S3 on the next
    pull. A path the manifest never saw is never deleted, so a half-pulled root cannot empty a prefix."""

    def __init__(self, name: str | None, local: Path | None):
        self.local = local
        self.name = name
        self.s3 = None
        if local is None:
            import boto3  # only the task needs it; a laptop test does not
            self.s3 = boto3.client("s3", region_name=aws_region())

    # -- listing: rel path -> (size, etag); the etag is None where none is known
    def _remote(self, prefix: str) -> dict[str, tuple[int, str | None]]:
        if self.local is not None:
            base = self.local / prefix
            return ({str(p.relative_to(base)): (p.stat().st_size, md5_of(p)) for p in base.rglob("*") if p.is_file()}
                    if base.exists() else {})
        out: dict[str, tuple[int, str | None]] = {}
        token = None
        while True:
            kw = {"Bucket": self.name, "Prefix": prefix.rstrip("/") + "/"}
            if token:
                kw["ContinuationToken"] = token
            res = self.s3.list_objects_v2(**kw)
            for o in res.get("Contents", []):
                out[o["Key"][len(kw["Prefix"]):]] = (o["Size"], (o.get("ETag") or "").strip('"') or None)
            token = res.get("NextContinuationToken")
            if not token:
                return out

    @staticmethod
    def _same(local: Path, remote: tuple[int, str | None]) -> bool:
        size, etag = remote
        if local.stat().st_size != size:
            return False
        if not etag or "-" in etag:          # multipart or unknown: the size is all there is to compare
            return True
        return md5_of(local) == etag

    @staticmethod
    def _manifest(root: Path) -> set[str]:
        p = root / MANIFEST
        try:
            return set(json.loads(p.read_text()).get("paths") or [])
        except (OSError, ValueError):
            return set()

    @staticmethod
    def _write_manifest(root: Path, paths: set[str]) -> None:
        (root / MANIFEST).write_text(json.dumps({"at": now(), "paths": sorted(paths)}))

    @staticmethod
    def _local(root: Path) -> dict[str, Path]:
        return {str(p.relative_to(root)): p for p in root.rglob("*") if p.is_file() and not excluded(str(p.relative_to(root)))}

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
        """One object, gone for good. Deletion goes both ways within one rule, the manifest: `push` removes
        from the bucket what the last sync saw on both sides and the root no longer holds (a marker a retry
        cleared), and `pull` removes from the root what the last sync saw on both sides and the bucket no
        longer holds (a work dir the backend deleted with its run, 2026-10-26). A path only one side ever
        had is never deleted: a missing file is more often a failed sync than a decision."""
        if self.local is not None:
            (self.local / key).unlink(missing_ok=True)
        else:
            self.s3.delete_object(Bucket=self.name, Key=key)

    def pull(self, prefix: str, root: Path) -> int:
        """Bring the seller's root down: files missing locally or different (size, then content), and
        remove from the root what the last sync saw on both sides and the bucket no longer holds (the
        backend deleted a run's work dirs with the run; without this the volume's copy came back on the
        next push and a deleted stop held the item again). Returns pulled + removed. The manifest
        afterwards names every path now on both sides."""
        n = 0
        remote = self._remote(prefix)
        for rel, meta in remote.items():
            if excluded(rel):
                continue
            dest = root / rel
            if dest.exists() and self._same(dest, meta):
                continue
            self.get(f"{prefix.rstrip('/')}/{rel}", dest)
            n += 1
        gone = [rel for rel in self._manifest(root) if rel not in remote and (root / rel).exists() and not excluded(rel)]
        for rel in gone:
            (root / rel).unlink(missing_ok=True)
            log(f"deleted from the root, gone from the bucket: {rel}")
            parent = (root / rel).parent
            while parent != root and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        self._write_manifest(root, {rel for rel in remote if not excluded(rel)} & set(self._local(root)))
        return n + len(gone)

    def push(self, root: Path, prefix: str) -> int:
        """Send the root back: files new or different since the last listing, and delete what the last
        sync saw on both sides and the root no longer holds. Secrets never go. Returns pushed + deleted."""
        remote = self._remote(prefix)
        local = self._local(root)
        n = 0
        for rel, p in local.items():
            if rel in remote and self._same(p, remote[rel]):
                continue
            self.put(p, f"{prefix.rstrip('/')}/{rel}")
            n += 1
        gone = [rel for rel in self._manifest(root) if rel not in local and rel in remote and not excluded(rel)]
        for rel in gone:
            self.delete(f"{prefix.rstrip('/')}/{rel}")
            log(f"deleted from the bucket, gone from the root: {rel}")
        self._write_manifest(root, set(local))
        return n + len(gone)


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

    def platform(self) -> dict[str, str]:
        """The platform's share of every env file, with no seller: DAPI_API_URL, ETSY_KEYSTRING,
        ETSY_SHARED_SECRET, FAL_KEY. A laptop test reads platform.json, or the same keys of credentials.json."""
        if self.local is not None:
            p = self.local / "platform.json"
            if p.exists():
                return json.loads(p.read_text())
            creds = json.loads((self.local / "credentials.json").read_text())
            return {k: v for k, v in creds.items() if k in PLATFORM_KEYS}
        return self._http("GET", "/internal/dapilot/platform/credentials")

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

    def event_doc(self, seller: str, doc: dict) -> None:
        """One of the director's events, forwarded as it was written, with the seller named. The backend
        validates it against its vendored copy of the plugin's event schema."""
        ev = {**doc, "seller": seller}
        if self.local is not None:
            with (self.local / "events.jsonl").open("a") as f:
                f.write(json.dumps(ev) + "\n")
            return
        try:
            self._http("POST", f"/internal/dapilot/runs/{doc.get('run')}/events", ev)
        except (urllib.error.URLError, OSError) as e:   # an event is telemetry; the run must not die for it
            log(f"event not delivered ({doc.get('kind')}): {e}")

    def event(self, seller: str, run: str, kind: str, **data) -> None:
        """The runner's own events (gate.answered, stage.retried, the supervisor's): not the director's,
        so not under the plugin's schema; the backend's own schema takes them flat."""
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


def template_release() -> dict:
    """What the plugin release owns in a seller's config: the `budget` block and the fal endpoints under
    `design` (the lifestyle photo model and the clip model with their prices). A seller's config.json is
    seeded once and then persisted, so a value the template changed later never reached an existing seller:
    the design worker's 1.50 USD of 2026-10-23 was the template's old budget, frozen in S3, and the clip
    engine switch of the same day would have been frozen the same way. Written under the plan limits on
    every job, so the release's figures apply and the plan's still win; the seller's palette and caps stay
    theirs. `stills` joined the release's keys on 2026-10-26, when the size went from 2048 to 1024 for the
    CPU-only host: a hosted seller never edits it, and the frozen 2048 would have kept the stall."""
    t = PLUGIN_DIR / "templates" / "config.example.json"
    try:
        tpl = json.loads(t.read_text())
    except (OSError, ValueError):
        return {}
    design = tpl.get("design") or {}
    out: dict = {"budget": tpl.get("budget") or {}}
    release_design = {k: design[k] for k in ("lifestyle_images", "lifestyle_video", "stills")
                      if isinstance(design.get(k), dict)}
    if release_design:
        out["design"] = release_design
    return out


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
    limits = template_release()
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
        # --preflight seller: the director checks the seller's own key per run; the platform's values
        # (Etsy app key, fal, Anthropic, the public API's address) were tested when this container
        # started (preflight below), so a problem with them fails the deploy and not a seller's run.
        cmd = [PYTHON, str(PLUGIN_DIR / "scripts" / "run_intake.py"), *args, "--billing", "api", "--preflight", "seller"]
        log(f"{self.seller}/{self.run}: {' '.join(cmd[2:])}")
        self.proc = subprocess.Popen(cmd, cwd=self.root, env=plugin_env(self.root), text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)

    def tail(self) -> dict:
        """Follows runs/<run>/events.jsonl while the director runs, posting each complete line as it appears,
        and drains stderr into this process's log in a thread so the director never blocks on a full pipe.
        Returns the director's stdout summary with its exit code."""
        assert self.proc is not None and self.proc.stderr is not None
        proc = self.proc
        tag = f"{self.seller}/{self.run}"

        def drain() -> None:
            for line in proc.stderr:
                log(f"{tag} | {line.rstrip(chr(10))[-300:]}")

        t = threading.Thread(target=drain, daemon=True, name="director-stderr")
        t.start()
        events = self.root / "runs" / self.run / EVENTS_FILE
        offset = 0
        while proc.poll() is None:
            offset = self._forward(events, offset)
            time.sleep(0.5)
        t.join(timeout=10)
        out = proc.stdout.read() if proc.stdout else ""
        proc.wait()
        self._forward(events, offset)
        summary = next((json.loads(c) for c in _json_candidates(out) if c), {})
        summary["exit"] = proc.returncode
        return summary

    def _forward(self, events: Path, offset: int) -> int:
        """Posts every complete line of the events file past `offset`; returns the new offset. A line
        the director is still writing (no newline yet) waits for the next look. A line that is not
        JSON is reported and skipped, never re-read."""
        if not events.exists():
            return offset
        with events.open("rb") as fh:
            fh.seek(offset)
            chunk = fh.read()
        if not chunk:
            return offset
        complete = chunk.rfind(b"\n") + 1
        for raw in chunk[:complete].splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                doc = json.loads(raw.decode("utf-8"))
            except ValueError:
                log(f"{self.seller}/{self.run}: events.jsonl holds a line that is not JSON; skipped: {raw[:120]!r}")
                continue
            self.backend.event_doc(self.seller, doc)
        return offset + complete

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


def ledger_outcome(root: Path, run: str) -> dict:
    """The run as one document, from the one reader of the work dirs: `ledger.py outcome` (2026-10-25).
    Before this the runner walked state.json, the markers and design.json itself, a second set of rules
    beside the ledger's. A run the director refused before registering anything is a valid outcome with
    `started` false; a ledger that cannot answer at all gets the same shape with the error in `notes`."""
    cmd = [PYTHON, str(PLUGIN_DIR / "scripts" / "ledger.py"), "outcome", "--run", run]
    try:
        res = subprocess.run(cmd, cwd=root, env=plugin_env(root), capture_output=True, text=True, timeout=120)
        doc = next((json.loads(c) for c in _json_candidates(res.stdout) if c), None)
    except (OSError, subprocess.TimeoutExpired) as e:
        res, doc = None, None
        err = f"{e.__class__.__name__}: {e}"
    else:
        err = (res.stderr or res.stdout)[-300:].strip() or f"exit {res.returncode}"
    if isinstance(doc, dict) and doc.get("items") is not None:
        return doc
    log(f"ledger outcome failed for {run}: {err}")
    return {"schema_version": SCHEMA_VERSION, "run": run, "at": now(), "started": False, "counts": {}, "cost": None,
            "gates_open": 0, "notes": [f"ledger outcome failed: {err}"], "items": []}


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
        description = str(body.get("description") or "").strip()
        d.start([str(intake), "--run", run, *(["--description", description] if description else []), *extra_args])
    elif kind == "approve":
        wd = root / "work" / body["item"]
        cmd = [PYTHON, str(PLUGIN_DIR / "scripts" / "approve_price.py"), "approve", "--workdir", str(wd), "--no-start",
               *(["--price", str(body["price"])] if body.get("price") is not None else []),
               *(["--by", str(body["by"])] if body.get("by") else [])]
        res = subprocess.run(cmd, cwd=root, env=plugin_env(root), capture_output=True, text=True)
        verdict = next((json.loads(c) for c in _json_candidates(res.stdout) if c), {"ok": False, "reason": res.stderr[-300:]})
        backend.event(seller, run, "gate.answered", item=body["item"], ok=bool(verdict.get("ok")), detail=verdict)
        if res.returncode != 0 or not verdict.get("ok"):
            # The outcome goes up as well as the event: the backend's lock on the gate is released by
            # either, so a lost event no longer leaves the buttons away until the next director ends.
            doc = ledger_outcome(root, run)
            doc["director"] = {"exit": 0, "skipped": f"approval refused: {verdict.get('reason')}"}
            backend.outcome(seller, run, doc)
            return {"ok": False, "reason": f"approval refused: {verdict.get('reason')}", "outcome": doc}
        if verdict.get("cached"):
            # The price was approved by an earlier message, whose director took the item on from there.
            # This one is a repeat (a second click, a redelivery): nothing to start. The outcome goes up so
            # the dashboard shows the item as it is, and the message is done.
            log(f"{seller}/{run}: {body['item'][:8]} already approved at {verdict.get('at')} by {verdict.get('by')}; nothing started")
            doc = ledger_outcome(root, run)
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
            doc = ledger_outcome(root, run)
            doc["director"] = {"exit": 0, "skipped": f"retry refused: {verdict.get('reason')}"}
            backend.outcome(seller, run, doc)
            return {"ok": False, "reason": f"retry refused: {verdict.get('reason')}", "outcome": doc}
        d.start(["--items", item, "--run", run, *extra_args])
    else:
        return {"ok": False, "reason": f"unknown message kind {kind!r}"}

    watcher = threading.Thread(target=lambda: (stop.wait(), d.stop()), daemon=True)
    watcher.start()
    summary = d.tail()
    # Pushed before the outcome is posted: the outcome names media keys the browser will ask the
    # backend to presign, and they must be in the bucket by the time the dashboard shows the row.
    pushed = bucket.push(root, f"sellers/{seller}")
    doc = ledger_outcome(root, run)
    doc["director"] = summary
    backend.outcome(seller, run, doc)
    log(f"{seller}/{run}: director exit {summary.get('exit')}"
        + (f" ({summary.get('reason')})" if summary.get("reason") else "") + f", {pushed} files pushed")
    return {"ok": summary.get("exit") in (0, 1) and not stop.is_set(), "summary": summary, "outcome": doc}


# ------------------------------------------------------------------ the platform's credentials, at start

PLATFORM_KEYS = ("DAPI_API_URL", "ETSY_KEYSTRING", "ETSY_SHARED_SECRET", "FAL_KEY")
PLATFORM_CHECKS = ("etsy", "fal", "claude")     # integrate.py's names; dapi is the seller's key, checked per run


def preflight(backend: Backend) -> int:
    """The platform's share of every seller's env file, tested against each platform before the consumer
    takes a message (2026-10-25): the Etsy app key, the fal key, the Anthropic key this container holds,
    and that the public API answers at its address. doctor.sh --serve runs this and exits on a failure,
    so a value missing or refused on the backend fails the deploy, where a person is watching, instead of
    every seller's run (staging refused every run for two days with `etsy: missing values`, 2026-10-23).
    The director still checks the seller's own 3DAPI key per run (`--preflight seller`). One line per
    check in doctor.sh's shape; the plugin's integrate.py does the asking against a throwaway store, so
    the rules (what counts as ok, that an Etsy shop need not be authorized) are the plugin's. Nothing paid."""
    try:
        values = backend.platform()
    except Exception as e:  # noqa: BLE001 - the backend is down or the route is missing: say which
        print(f"FAIL  backend: platform credentials not served ({e.__class__.__name__}: {e})")
        return 1
    failed = 0
    url = str(values.get("DAPI_API_URL") or "").strip().rstrip("/")
    if not url:
        print("FAIL  dapi: DAPI_API_URL is empty on the backend (DAPILOT_PUBLIC_API_URL)")
        failed += 1
    else:
        # No seller key here, so /v1/me is expected to refuse: any HTTP answer proves the address.
        status, err = None, None
        try:
            with urllib.request.urlopen(urllib.request.Request(url + "/v1/me", headers={"Accept": "application/json"}), timeout=15) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        except Exception as e:  # noqa: BLE001
            err = e
        if status is not None:
            print(f"ok    dapi: {url} answers (HTTP {status}; each seller's key is checked by their run)")
        else:
            print(f"FAIL  dapi: {url} does not answer ({err.__class__.__name__}: {err})")
            failed += 1
    store = Path(tempfile.mkdtemp(prefix="dapilot-preflight-"))
    try:
        write_env(store, {**values, "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "")})
        env = {**os.environ, "DAPILOT_STORE_DIR": str(store), "DAPILOT_PROJECT_DIR": str(store)}
        for iid in PLATFORM_CHECKS:
            try:
                p = subprocess.run([PYTHON, str(PLUGIN_DIR / "scripts" / "integrate.py"), "test", "--integration", iid],
                                   capture_output=True, text=True, env=env, timeout=120)
                res = next((json.loads(c) for c in _json_candidates(p.stdout) if c), None) \
                    or {"ok": False, "summary": (p.stderr or p.stdout)[-200:].strip() or "no answer"}
            except (OSError, subprocess.TimeoutExpired) as e:
                res = {"ok": False, "summary": f"{e.__class__.__name__}: {e}"}
            # The Etsy app key passed and no shop is authorized: shops are the sellers' and come per run.
            ok = bool(res.get("ok")) or (iid == "etsy" and res.get("action") == "authorize")
            print(f"{'ok   ' if ok else 'FAIL '} {iid}: {res.get('summary') or ('ok' if ok else 'not ok')}")
            failed += 0 if ok else 1
    finally:
        shutil.rmtree(store, ignore_errors=True)
    return 1 if failed else 0


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
    pf = sub.add_parser("preflight", help="test the platform's credentials against each platform, one line each; exit 1 on a failure")
    pf.add_argument("--local-backend", help="a directory standing in for the backend (platform.json or credentials.json)")
    a = ap.parse_args()
    if not (PLUGIN_DIR / "scripts" / "run_intake.py").exists():
        print(json.dumps({"ok": False, "reason": f"no plugin at {PLUGIN_DIR}; set DAPILOT_PLUGIN_DIR"})); return 2
    local_b = Path(a.local_bucket).resolve() if getattr(a, "local_bucket", None) else None
    local_k = Path(a.local_backend).resolve() if getattr(a, "local_backend", None) else None
    backend = Backend(os.environ.get("DAPILOT_BACKEND_URL"), os.environ.get("DAPILOT_BACKEND_TOKEN"), local_k)
    if a.cmd == "preflight":
        return preflight(backend)
    bucket = Bucket(os.environ.get("DAPILOT_BUCKET"), local_b)
    extra = [x for x in getattr(a, "director_args", "").split() if x]
    if a.cmd == "serve":
        return serve(backend, bucket, extra)
    body = json.loads(Path(a.message).read_text())
    result = handle(body, backend, bucket, extra, threading.Event())
    print(json.dumps({k: v for k, v in result.items() if k != "outcome"}, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
