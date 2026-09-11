#!/usr/bin/env python3
"""Quote one model file through dapi: upload, create, poll. Deterministic and resumable.

  quote.py --workdir DIR [--max-wait SECONDS] [--store-id ID]

Reads DIR/source.<ext> and DIR/meta.json (sha256). Every dapi call goes through
dapi.py so it is validated against the live contract. The quote is named with
the file's sha256 so it can always be found again:

  1. DIR/quote.pending.json exists  -> resume polling that publicId
  2. listQuotes has a quote named <sha256> that is not ERROR/CANCELLED -> poll it
  3. otherwise uploadQuoteFile -> PUT bytes -> createQuote -> poll

Poll until DONE, ERROR or CANCELLED, or --max-wait runs out. Outcomes:
  DONE       writes DIR/quote.json, exit 0                      {"ok": true, "status": "DONE", ...}
  ERROR      writes DIR/quote.error.json (not quote.json), exit 1 {"ok": false, "status": "ERROR", "error": "<slicer message>"}
  timeout    keeps DIR/quote.pending.json, exit 3                {"ok": false, "status": "PROCESSING"}
  other      exit 1 with reason
quote.json is only ever written for a DONE quote, so its existence means "priced".

store_id: --store-id, else DAPI_STORE_ID, else the account's only store, else
omitted (older accounts). Two or more stores without a choice is an error.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DAPI = HERE / "dapi.py"
TERMINAL = {"DONE", "ERROR", "CANCELLED"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def out(obj: dict, code: int) -> int:
    print(json.dumps(obj, indent=2))
    return code


def dapi(*args: str) -> dict:
    p = subprocess.run([sys.executable, str(DAPI), *args], capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "status": None, "reason": f"dapi.py produced no JSON: {(p.stderr or p.stdout)[:300]}"}


def call(op: str, *args: str) -> dict:
    r = dapi("call", op, *args)
    if not r.get("ok"):
        body = r.get("body")
        msg = body.get("message") if isinstance(body, dict) else body
        raise RuntimeError(f"{op} -> HTTP {r.get('status')}: {msg or r.get('reason')}")
    return r["body"]


def pick_store(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    env = os.environ.get("DAPI_STORE_ID", "").strip()
    if env:
        return env
    try:
        body = call("listStores")
    except RuntimeError:
        return None  # accounts without the stores feature
    items = body.get("items", body) if isinstance(body, dict) else body
    items = [s for s in (items or []) if not s.get("archived")]
    if len(items) == 1:
        return items[0].get("id") or items[0].get("storeId") or items[0].get("publicId")
    if len(items) == 0:
        return None
    raise RuntimeError(f"account has {len(items)} stores; set DAPI_STORE_ID to choose one")


def find_existing(name: str, store_id: str | None) -> dict | None:
    page = 1
    while page <= 20:
        args = ["--query", f"page={page}", "--query", "page_size=100"]
        if store_id:
            args += ["--query", f"store_id={store_id}"]
        body = call("listQuotes", *args)
        items = body.get("items", [])
        for q in items:
            if q.get("name") == name and q.get("status") not in ("ERROR", "CANCELLED"):
                return q
        if len(items) < 100 or page * 100 >= int(body.get("total", 0)):
            return None
        page += 1
    return None


def poll(public_id: str, max_wait: int) -> dict:
    deadline = time.time() + max_wait
    delay = 10
    while True:
        q = call("getQuoteByPublicId", "--path", f"publicId={public_id}")
        if q.get("status") in TERMINAL:
            return q
        if time.time() >= deadline:
            return q
        time.sleep(min(delay, max(1, deadline - time.time())))
        delay = min(delay * 1.5, 60)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--max-wait", type=int, default=480, help="seconds to wait for the slicer (default 480)")
    ap.add_argument("--store-id")
    a = ap.parse_args()
    wd = Path(a.workdir)
    if (wd / "quote.json").exists():
        return out({"ok": True, "status": "DONE", "cached": True, "quote": json.loads((wd / "quote.json").read_text())}, 0)
    sources = [p for p in wd.iterdir() if p.stem == "source"]
    if not sources:
        return out({"ok": False, "reason": "no source.<ext> in workdir"}, 1)
    src = sources[0]
    meta = json.loads((wd / "meta.json").read_text()) if (wd / "meta.json").exists() else {}
    name = meta.get("sha256") or src.name
    if not os.environ.get("DAPI_API_KEY") or not os.environ.get("DAPI_API_URL"):
        return out({"ok": False, "reason": "DAPI_API_KEY or DAPI_API_URL not set"}, 2)
    pending_path = wd / "quote.pending.json"
    t0 = time.time()
    try:
        store_id = pick_store(a.store_id)
        pending = json.loads(pending_path.read_text()) if pending_path.exists() else None
        if pending:
            public_id, s3_key, created = pending["publicId"], pending.get("s3_key"), False
        else:
            existing = find_existing(name, store_id)
            if existing:
                public_id, s3_key, created = existing["publicId"], None, False
            else:
                body_file = wd / "req.uploadQuoteFile.json"
                body_file.write_text(json.dumps({"file_name": src.name}))
                up = call("uploadQuoteFile", "--body", str(body_file))
                r = dapi("upload", up["upload_url"], str(src))
                if not r.get("ok"):
                    raise RuntimeError(f"S3 upload failed: {r.get('reason') or r.get('status')}")
                s3_key = up["s3_key"]
                req = {"s3_key": s3_key, "name": name}
                if store_id:
                    req["store_id"] = store_id
                (wd / "req.createQuote.json").write_text(json.dumps(req))
                cq = call("createQuote", "--body", str(wd / "req.createQuote.json"))
                public_id, created = cq["publicId"], True
            pending_path.write_text(json.dumps({"publicId": public_id, "s3_key": s3_key, "name": name,
                                                "store_id": store_id, "created": created, "at": now()}))
        q = poll(public_id, a.max_wait)
    except RuntimeError as e:
        return out({"ok": False, "status": None, "reason": str(e)}, 1)

    elapsed = round(time.time() - t0, 1)
    record = {"publicId": public_id, "name": name, "store_id": store_id, "status": q.get("status"),
              "unit_price": q.get("result"), "metrics": q.get("metrics"), "batch": q.get("batch"), "errorMessage": q.get("errorMessage"),
              "createdAt": q.get("createdAt"), "finishedAt": q.get("finishedAt"), "fetched_at": now(),
              "seconds_waited": elapsed, "raw": q}
    if q.get("status") == "DONE":
        (wd / "quote.json").write_text(json.dumps(record, indent=2))
        pending_path.unlink(missing_ok=True)
        return out({"ok": True, "status": "DONE", "publicId": public_id, "unit_price": q.get("result"),
                    "metrics": q.get("metrics"), "batch": q.get("batch"), "seconds_waited": elapsed}, 0)
    if q.get("status") in ("ERROR", "CANCELLED"):
        (wd / "quote.error.json").write_text(json.dumps(record, indent=2))
        pending_path.unlink(missing_ok=True)
        return out({"ok": False, "status": q.get("status"), "publicId": public_id,
                    "error": q.get("errorMessage") or "no message", "seconds_waited": elapsed}, 1)
    return out({"ok": False, "status": q.get("status"), "publicId": public_id,
                "reason": f"still {q.get('status')} after {elapsed}s; quote.pending.json kept, rerun resumes polling"}, 3)


if __name__ == "__main__":
    raise SystemExit(main())
