#!/usr/bin/env python3
"""Turn a studio still into a lifestyle photograph with an image-edit model on fal.

  generate_image.py --reference PNG [--reference PNG] --prompt "..." --out PATH.png
                    [--endpoint fal-ai/nano-banana-2/edit] [--aspect 1:1]
                    [--resolution 2K] [--seed 1] [--deadline 0]

The references are our own renders of the real mesh. The prompt places that
object in a scene; the model is told to keep the object exactly as shown, so
the shape a buyer sees is the shape they get.

Writes PATH, PATH.request.json the moment fal accepts the request, and PATH.json
as the record. A second run with the output present prints cached; with only
the request file present it attaches to that request instead of paying again.
Prints one JSON object. Exit 0 done, 1 failed, 2 usage or environment, 3 still
running on fal after --deadline seconds (run the same command again).

Needs FAL_KEY or FAL_API_KEY. Spends money: one image per run.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fal_queue  # noqa: E402

DEFAULT_ENDPOINT = "fal-ai/nano-banana-2/edit"
ASPECTS = ("auto", "21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16")
KEEP = ("Keep the product from the reference image exactly as it is: the same shape, proportions, "
        "number of parts, colour and surface. Do not add text, logos, watermarks or extra copies of the product.")


def fail(reason: str, code: int = 1, **extra) -> int:
    print(json.dumps({"ok": False, "reason": reason, **extra}))
    return code


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", action="append", required=True, help="our render; repeat for up to 3")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--aspect", default="1:1", choices=ASPECTS)
    ap.add_argument("--resolution", default="2K", help="0.5K, 1K, 2K or 4K where the endpoint takes it")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--deadline", type=float, default=0, help="seconds to wait before exiting 3; 0 waits")
    ap.add_argument("--force", action="store_true", help="submit again even if an output or request exists")
    a = ap.parse_args()

    out = Path(a.out).resolve()
    req_p = out.with_name(out.name + ".request.json")
    rec_p = out.with_name(out.name + ".json")
    if out.exists() and not a.force:
        print(json.dumps({"ok": True, "cached": True, "image": str(out)}))
        return 0

    refs = [Path(r).resolve() for r in a.reference]
    if len(refs) > 3:
        return fail("at most 3 references", 2)
    for r in refs:
        if not r.exists():
            return fail(f"no reference at {r}", 2)

    k = fal_queue.key()
    if not k:
        return fail("neither FAL_KEY nor FAL_API_KEY is set; put one in store/.env", 2)
    problem = fal_queue.preflight(k)
    if problem:
        return fail(problem, 2)

    import fal_client

    t0 = time.time()
    held = None if a.force else fal_queue.pending(req_p)
    if held:
        endpoint, request_id = held["endpoint"], held["request_id"]
        fal_queue.note(f"attaching to {request_id}, submitted {held.get('submitted')}")
        recorded = held.get("request", {})
    else:
        endpoint = a.endpoint
        try:
            urls = [fal_client.upload_file(str(r)) for r in refs]
        except Exception as exc:
            return fail(f"uploading the reference failed: {exc}")
        payload = {
            "prompt": f"{a.prompt.strip()} {KEEP}" + (
                " The reference images are the same single product seen from different sides; the first one "
                "sets the viewing angle. Show one product." if len(refs) > 1 else ""),
            "image_urls": urls,
            "num_images": 1,
            "aspect_ratio": a.aspect,
            "resolution": a.resolution,
            "output_format": "png",
            "seed": a.seed,
        }
        recorded = {**{k2: v for k2, v in payload.items() if k2 != "image_urls"},
                    "references": [r.name for r in refs]}
        try:
            request_id = fal_queue.submit(endpoint, payload, req_p, recorded)
        except Exception as exc:
            return fail(f"submit failed: {exc}", endpoint=endpoint)
        fal_queue.note(f"submitted {request_id}")

    try:
        result = fal_queue.wait(endpoint, request_id, a.deadline, fal_queue.note)
    except Exception as exc:
        return fail(f"generation failed: {exc}", request_id=request_id)
    if result is None:
        print(json.dumps({"ok": False, "pending": True, "request_id": request_id,
                          "reason": f"still running on fal after {a.deadline:g}s; run the same command again"}))
        return fal_queue.PENDING_EXIT

    images = result.get("images") or []
    url = images[0].get("url") if images else None
    if not url:
        return fail("fal returned no image", result_keys=sorted(result), request_id=request_id)
    try:
        fal_queue.download(url, out)
    except Exception as exc:
        return fail(f"could not download the result: {exc}", request_id=request_id)

    record = {"ok": True, "endpoint": endpoint, "request_id": request_id, "image": out.name,
              "bytes": out.stat().st_size, "width": images[0].get("width"), "height": images[0].get("height"),
              "description": result.get("description"), "request": recorded,
              "wall_s": round(time.time() - t0, 1)}
    rec_p.write_text(json.dumps(record, indent=2))
    print(json.dumps(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
