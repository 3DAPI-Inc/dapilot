#!/usr/bin/env python3
"""Upscale a clip with Topaz on fal.

  upscale_video.py --video PATH [--factor 2] [--model Proteus] [--recover-detail 0.5]

Writes PATH with `_up` appended, and a record beside it. Prints one JSON object.
Exit 0 on success.

Separate from generate_video.py on purpose: generating and enlarging are two
decisions, and you will often want to enlarge something that was never
generated, such as the browser turntable.

Needs FAL_KEY or FAL_API_KEY in env. Spends money: about ten cents for five
seconds at 1080p.

Note for self-hosting later: Wan is open weights and can move onto your own
GPUs. Topaz is licensed commercial software and cannot. If the plan is to run
everything on your own hardware, the open upscalers, SeedVR2 or Real-ESRGAN,
are the ones that travel.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "fal-ai/topaz/upscale/video"
MODELS = ("Proteus", "Iris", "Rhea", "Gaia", "Theia", "Artemis")


def fail(reason: str, **extra) -> int:
    print(json.dumps({"ok": False, "reason": reason, **extra}))
    return 1


def preflight(key: str) -> str | None:
    req = urllib.request.Request(
        "https://rest.fal.ai/storage/auth/token?storage_type=fal-cdn-v3",
        data=b"{}", method="POST",
        headers={"authorization": f"Key {key}", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30):
            return None
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read()).get("detail") or f"fal returned HTTP {exc.code}"
        except Exception:
            return f"fal returned HTTP {exc.code}"
    except Exception as exc:
        return f"could not reach fal: {exc}"


def follow(handle, note) -> dict:
    """Same live view as the generator: a slow queue must not look like a hang."""
    seen, last = 0, None
    for event in handle.iter_events(with_logs=True, interval=2.0):
        kind = type(event).__name__
        position = getattr(event, "position", None)
        if kind != last or position is not None:
            note(f"  {kind.lower()}" + (f", {position} ahead in the queue" if position is not None else ""))
            last = kind
        for entry in (getattr(event, "logs", None) or [])[seen:]:
            note("  " + str(entry.get("message", entry) if isinstance(entry, dict) else entry))
            seen += 1
    return handle.get()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", required=True)
    ap.add_argument("--factor", type=float, default=2.0, help="960 x2 lands just past 1080")
    ap.add_argument("--model", default="Proteus", choices=MODELS)
    ap.add_argument("--recover-detail", type=float, default=None,
                    help="0 to 1. The lever that matters on a soft source.")
    ap.add_argument("--target-fps", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    key = (os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY") or "").strip()
    if not key:
        return fail("neither FAL_KEY nor FAL_API_KEY is set")
    os.environ["FAL_KEY"] = key

    src = Path(a.video).resolve()
    if not src.exists():
        return fail(f"no video at {src}")
    out = Path(a.out) if a.out else src.with_name(f"{src.stem}_up{src.suffix}")
    if out.exists() and not a.force:
        print(json.dumps({"ok": True, "cached": True, "video": str(out)}))
        return 0

    problem = preflight(key)
    if problem:
        return fail(problem)

    import fal_client
    note = lambda line: print(line, file=sys.stderr, flush=True)  # noqa: E731

    t0 = time.time()
    try:
        video_url = fal_client.upload_file(str(src))
    except Exception as exc:
        return fail(f"upload failed: {exc}")

    payload: dict = {"video_url": video_url, "model": a.model, "upscale_factor": a.factor,
                     "H264_output": True}
    if a.recover_detail is not None:
        payload["recover_detail"] = a.recover_detail
    if a.target_fps:
        payload["target_fps"] = a.target_fps

    try:
        handle = fal_client.submit(ENDPOINT, arguments=payload)
    except Exception as exc:
        return fail(f"submit failed: {exc}")
    note(f"submitted {handle.request_id}")
    try:
        result = follow(handle, note)
    except Exception as exc:
        return fail(f"upscale failed: {exc}", request_id=handle.request_id)

    url = (result.get("video") or {}).get("url")
    if not url:
        return fail("fal returned no video url", result_keys=sorted(result))
    try:
        with urllib.request.urlopen(url, timeout=600) as res:
            out.write_bytes(res.read())
    except Exception as exc:
        return fail(f"could not download the result: {exc}", url=url)

    record = {"ok": True, "endpoint": ENDPOINT, "source": src.name, "video": out.name,
              "video_bytes": out.stat().st_size,
              "request": {k: v for k, v in payload.items() if k != "video_url"},
              "wall_s": round(time.time() - t0, 1)}
    out.with_suffix(".json").write_text(json.dumps(record, indent=2))
    print(json.dumps(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
