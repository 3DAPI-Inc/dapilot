#!/usr/bin/env python3
"""Submit to fal's queue once, and never pay twice for the same request.

Shared by generate_image.py and generate_video.py; not a CLI.

The rule that matters: a request is written to `<name>.request.json` the moment
fal accepts it, before the first poll. Any later run that finds that file
attaches to the request instead of submitting a new one, so a killed worker, a
Bash timeout or a deadline costs a second look, never a second generation.

`wait()` takes a deadline. When it passes the request is still running on fal
and the caller exits 3; running the same command again picks it up.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

PENDING_EXIT = 3


def key() -> str | None:
    """fal_client reads FAL_KEY; store/.env names it FAL_API_KEY. Accept both."""
    k = (os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY") or "").strip()
    if k:
        os.environ["FAL_KEY"] = k
    return k or None


def preflight(k: str) -> str | None:
    """A cheap authenticated call up front, so an empty balance reads as that
    and not as a bare 403 from an upload URL nobody recognises."""
    req = urllib.request.Request(
        "https://rest.fal.ai/storage/auth/token?storage_type=fal-cdn-v3",
        data=b"{}", method="POST",
        headers={"authorization": f"Key {k}", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30):
            return None
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail")
        except Exception:
            detail = None
        return detail or f"fal rejected the key with HTTP {exc.code}"
    except Exception as exc:
        return f"could not reach fal: {exc}"


def pending(request_path: Path) -> dict | None:
    """The request this output is already waiting on, if any."""
    if not request_path.exists():
        return None
    try:
        rec = json.loads(request_path.read_text())
    except json.JSONDecodeError:
        return None
    return rec if rec.get("request_id") and rec.get("endpoint") else None


def submit(endpoint: str, payload: dict, request_path: Path, recorded: dict) -> str:
    """Submit and write the request file before anything else can go wrong."""
    import fal_client
    handle = fal_client.submit(endpoint, arguments=payload)
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(json.dumps({
        "request_id": handle.request_id, "endpoint": endpoint,
        "submitted": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "request": recorded,
    }, indent=2))
    return handle.request_id


def wait(endpoint: str, request_id: str, deadline_s: float, note: Callable[[str], None]) -> dict | None:
    """Poll until done. Returns the result, or None when the deadline passed first.

    deadline_s <= 0 waits as long as fal takes. Raises on a failed request.
    """
    import fal_client
    t0 = time.time()
    seen = 0
    last = None
    while True:
        status = fal_client.status(endpoint, request_id, with_logs=True)
        kind = type(status).__name__
        position = getattr(status, "position", None)
        if kind != last or position is not None:
            note(f"  {kind.lower()}" + (f", {position} ahead in the queue" if position is not None else ""))
            last = kind
        logs = getattr(status, "logs", None) or []
        for entry in logs[seen:]:
            note("  " + str(entry.get("message", entry) if isinstance(entry, dict) else entry))
        seen = max(seen, len(logs))
        if kind == "Completed":
            if getattr(status, "error", None):
                raise RuntimeError(f"fal reported {status.error_type or 'an error'}: {status.error}")
            return fal_client.result(endpoint, request_id)
        if deadline_s > 0 and time.time() - t0 > deadline_s:
            return None
        time.sleep(3)


def download(url: str, dst: Path, timeout: int = 600) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    with urllib.request.urlopen(url, timeout=timeout) as res:
        tmp.write_bytes(res.read())
    tmp.replace(dst)


def note(line: str) -> None:
    """Progress on stderr, flushed. stdout stays one JSON object."""
    import sys
    print(line, file=sys.stderr, flush=True)
