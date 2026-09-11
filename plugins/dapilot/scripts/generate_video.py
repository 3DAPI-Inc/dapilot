#!/usr/bin/env python3
"""Turn a captured turntable into a photoreal clip with Wan, on fal.

  generate_video.py --workdir DIR --source depth  [--prompt "..."] [--frames 120]
  generate_video.py --workdir DIR --source beauty [--prompt "..."]

--source depth   sends our ground-truth depth sequence with preprocess off, so
                 the model is conditioned on the real geometry of the mesh.
--source beauty  sends the plain turntable with preprocess on, so fal estimates
                 depth itself. The fallback, and the control in a comparison.

Writes DIR/renders/<name>.mp4 and a record at DIR/renders/<name>.json. Prints one
JSON object. Exit 0 on success.

Needs FAL_KEY in env (store/.env), ffmpeg, and a captured sequence from
capture_turntable.py. Spends money: every run is a real generation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINTS = {
    # Depth-conditioned. Geometry is locked per frame; caps at 720p.
    "vace": "fal-ai/wan-22-vace-fun-a14b/depth",
    # Reference-to-video. No structural control, but 1080p and it accepts up to
    # three reference clips, which a full turntable suits unusually well.
    "ref": "wan/v2.6/reference-to-video",
}
ENDPOINT = ENDPOINTS["vace"]

DEFAULT_PROMPT = (
    "a product photograph of a matte 3D printed object on a soft studio backdrop, "
    "even diffused lighting, shallow depth of field, high detail, photographic"
)
NEGATIVE = "cartoon, illustration, distorted geometry, extra objects, text, watermark"


def follow(handle, note) -> dict:
    """Print queue position and logs as they arrive, then return the result.

    fal_client.subscribe() blocks and swallows everything, which makes a slow
    run indistinguishable from a hung one. iter_events yields the same status
    objects the queue already publishes, so there is no reason to be blind.
    """
    seen = 0
    last = None
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


def preflight(key: str) -> str | None:
    """Ask fal for a storage token before doing any work.

    The client turns a rejected key into a bare 403 from a URL nobody
    recognises. fal's own body says exactly what is wrong, most often an
    empty balance, so a cheap call up front turns a confusing failure into
    an instruction.
    """
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
            detail = json.loads(exc.read()).get("detail")
        except Exception:
            detail = None
        return detail or f"fal rejected the key with HTTP {exc.code}"
    except Exception as exc:
        return f"could not reach fal: {exc}"


def fail(reason: str, **extra) -> int:
    print(json.dumps({"ok": False, "reason": reason, **extra}))
    return 1


def encode_conditioning(frames: Path, out: Path, fps: int) -> tuple[bool, str]:
    """Near-lossless, because the control signal must not be compressed away.

    crf 0 so smooth depth gradients do not band into terraces. yuv420p is safe
    here even though it subsamples chroma: a depth map is greyscale, so all of
    the signal is in luma, which is never subsampled. A normal map would need
    yuv444p, and is not what this sends.
    """
    cmd = [
        "ffmpeg", "-y", "-framerate", str(fps), "-i", str(frames / "frame_%04d.png"),
        "-c:v", "libx264", "-preset", "veryslow", "-crf", "0",
        "-pix_fmt", "yuv420p", "-an", str(out),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    tail = (p.stderr or "").strip().splitlines()
    return p.returncode == 0, (tail[-1] if tail else "") if p.returncode else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--engine", choices=sorted(ENDPOINTS), default="vace",
                    help="vace: depth-conditioned, 720p. ref: Wan 2.6 reference-to-video, 1080p.")
    ap.add_argument("--endpoint", default=None, help="override the endpoint id")
    ap.add_argument("--videos", nargs="*", default=None,
                    help="ref engine: clips to send as @Video1, @Video2, @Video3")
    ap.add_argument("--duration", type=int, default=5, choices=[5, 10])
    ap.add_argument("--source", choices=["depth", "beauty"], default="depth")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--negative", default=NEGATIVE)
    ap.add_argument("--frames", type=int, default=120)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--resolution", default="720p",
                    choices=["auto", "240p", "360p", "480p", "580p", "720p", "1080p"],
                    help="the vace engine caps at 720p; 1080p is the ref engine only")
    ap.add_argument("--aspect", default="1:1", choices=["auto", "16:9", "1:1", "9:16"])
    ap.add_argument("--guidance", type=float, default=5.0)
    ap.add_argument("--steps", type=int, default=30,
                    help="denoising steps. Billing is per video second, not per step, "
                         "so more steps buy quality for time rather than money.")
    ap.add_argument("--reference", metavar="PNG",
                    help="a beauty frame sent as first_frame_url. Anchors colour, material "
                         "and lighting to our render while depth drives the geometry. "
                         "Defaults to renders/beauty/frame_0000.png when it exists.")
    ap.add_argument("--no-reference", action="store_true", help="send depth alone")
    ap.add_argument("--reference-mode", choices=["ref", "first"], default="ref",
                    help="ref guides appearance only; first pins frame 0 to the image. "
                         "Use ref when the prompt changes the setting, or frame 0 will "
                         "be our studio sweep and the clip will morph out of it.")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--name", default=None, help="output stem; defaults to lifestyle_<source>")
    ap.add_argument("--force", action="store_true", help="regenerate even if the output exists")
    ap.add_argument("--attach", metavar="REQUEST_ID",
                    help="follow a request already in flight instead of submitting a new one")
    a = ap.parse_args()

    # fal_client reads FAL_KEY; accept FAL_API_KEY too, since that is the name
    # the rest of store/.env uses for credentials.
    key = (os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY") or "").strip()
    if not key:
        return fail("neither FAL_KEY nor FAL_API_KEY is set; put one in store/.env and source it")
    os.environ["FAL_KEY"] = key
    problem = preflight(key)
    if problem:
        return fail(problem)
    if not shutil.which("ffmpeg"):
        return fail("ffmpeg not found")

    wd = Path(a.workdir).resolve()

    if a.attach:
        import fal_client
        note = lambda line: print(line, file=sys.stderr, flush=True)  # noqa: E731
        handle = fal_client.SyncRequestHandle.from_request_id(ENDPOINT, a.attach)
        note(f"attached to {a.attach}")
        try:
            result = follow(handle, note)
        except Exception as exc:
            return fail(f"following {a.attach} failed: {exc}")
        print(json.dumps({"ok": True, "attached": a.attach, "result": result}))
        return 0

    endpoint = a.endpoint or ENDPOINTS[a.engine]

    if a.engine == "ref":
        clips = [Path(v) for v in (a.videos or [])]
        if not clips:
            return fail("--videos is required for the ref engine (1 to 3 clips)")
        if len(clips) > 3:
            return fail("the ref engine takes at most 3 videos")
        for c in clips:
            if not c.exists():
                return fail(f"no video at {c}")
            if c.stat().st_size > 30 * 1024 * 1024:
                return fail(f"{c.name} is over the 30 MB per-clip limit")
        name = a.name or "reference_video"
        out_mp4 = wd / "renders" / f"{name}.mp4"
        if out_mp4.exists() and not a.force:
            print(json.dumps({"ok": True, "cached": True, "video": str(out_mp4)}))
            return 0
        problem = preflight(key)
        if problem:
            return fail(problem)
        import fal_client
        note = lambda line: print(line, file=sys.stderr, flush=True)  # noqa: E731
        t0 = time.time()
        urls = []
        for c in clips:
            try:
                urls.append(fal_client.upload_file(str(c)))
            except Exception as exc:
                return fail(f"uploading {c.name} failed: {exc}")
            note(f"uploaded {c.name} as @Video{len(urls)}")
        payload = {
            "prompt": a.prompt,
            "negative_prompt": a.negative[:500],
            "video_urls": urls,
            # This endpoint offers only 720p and 1080p, and 1080p is the reason to use it.
            "resolution": "720p" if a.resolution == "720p" else "1080p",
            "duration": a.duration,
            "aspect_ratio": a.aspect if a.aspect != "auto" else "1:1",
            "seed": a.seed,
            # One continuous scene, and our own wording rather than a rewrite.
            "multi_shots": False,
            "enable_prompt_expansion": False,
        }
        try:
            handle = fal_client.submit(endpoint, arguments=payload)
        except Exception as exc:
            return fail(f"submit failed: {exc}", endpoint=endpoint)
        (wd / "renders" / f"{name}.request.json").write_text(json.dumps(
            {"request_id": handle.request_id, "endpoint": endpoint, "clips": [c.name for c in clips],
             "request": {k: v for k, v in payload.items() if k != "video_urls"}}, indent=2))
        note(f"submitted {handle.request_id}")
        try:
            result = follow(handle, note)
        except Exception as exc:
            return fail(f"generation failed: {exc}", request_id=handle.request_id)
        url = (result.get("video") or {}).get("url")
        if not url:
            return fail("fal returned no video url", result_keys=sorted(result))
        with urllib.request.urlopen(url, timeout=600) as res:
            out_mp4.write_bytes(res.read())
        record = {"ok": True, "endpoint": endpoint, "engine": "ref",
                  "clips": [c.name for c in clips], "video": out_mp4.name,
                  "video_bytes": out_mp4.stat().st_size,
                  "request": {k: v for k, v in payload.items() if k != "video_urls"},
                  "wall_s": round(time.time() - t0, 1)}
        (wd / "renders" / f"{name}.json").write_text(json.dumps(record, indent=2))
        print(json.dumps(record))
        return 0

    frames_dir = wd / "renders" / a.source
    shots = sorted(frames_dir.glob("frame_*.png"))
    if not shots:
        return fail(f"no {a.source} frames in {frames_dir}; run capture_turntable.py first")

    name = a.name or f"lifestyle_{a.source}"
    out_mp4 = wd / "renders" / f"{name}.mp4"
    record_p = wd / "renders" / f"{name}.json"
    if out_mp4.exists() and not a.force:
        print(json.dumps({"ok": True, "cached": True, "video": str(out_mp4)}))
        return 0

    # preprocess off means "this video IS the control signal"; on means
    # "estimate depth from this video first". That single flag is the whole
    # difference between our geometry and a guess at it.
    preprocess = a.source == "beauty"

    cond = wd / "renders" / f"_conditioning_{a.source}.mp4"
    ok, why = encode_conditioning(frames_dir, cond, a.fps)
    if not ok:
        return fail(f"ffmpeg failed encoding the conditioning video: {why}")

    import fal_client                                  # imported late: no key, no need

    t0 = time.time()
    try:
        video_url = fal_client.upload_file(str(cond))
    except Exception as exc:
        return fail(f"upload to fal failed: {exc}")

    reference = None
    if not a.no_reference:
        chosen = Path(a.reference) if a.reference else (wd / "renders" / "beauty" / "frame_0000.png")
        if chosen.exists():
            try:
                reference = fal_client.upload_file(str(chosen))
                note_early = f"reference frame: {chosen.name}"
            except Exception as exc:
                return fail(f"uploading the reference frame failed: {exc}")
        elif a.reference:
            return fail(f"no reference frame at {chosen}")
        else:
            note_early = "no reference frame found; depth alone"
    else:
        note_early = "reference disabled; depth alone"
    print(note_early, file=sys.stderr, flush=True)

    payload = {
        "prompt": a.prompt,
        "negative_prompt": a.negative,
        "video_url": video_url,
        "preprocess": preprocess,
        "num_frames": min(max(a.frames, 81), 241),
        "frames_per_second": a.fps,
        "resolution": a.resolution,
        "aspect_ratio": a.aspect,
        "guidance_scale": a.guidance,
        "num_inference_steps": a.steps,
        "seed": a.seed,
        # Lock the output timing to what we sent, rather than letting the
        # endpoint renormalise 120 frames at 24fps into its own default.
        "match_input_num_frames": True,
        "match_input_frames_per_second": True,
    }
    if reference:
        if a.reference_mode == "first":
            payload["first_frame_url"] = reference
        else:
            payload["ref_image_urls"] = [reference]

    def note(line: str) -> None:
        """Progress goes to stderr, flushed. stdout stays one JSON object."""
        print(line, file=sys.stderr, flush=True)

    try:
        handle = fal_client.submit(endpoint, arguments=payload)
    except Exception as exc:
        return fail(f"submit failed: {exc}", sent={k: v for k, v in payload.items() if k != "video_url"})

    # Written before the first poll so another shell can attach with --attach,
    # and so a killed run leaves behind the one thing needed to recover it.
    req_p = wd / "renders" / f"{name}.request.json"
    req_p.write_text(json.dumps({"request_id": handle.request_id, "endpoint": ENDPOINT,
                                 "submitted": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                 "request": {k: v for k, v in payload.items() if k != "video_url"}}, indent=2))
    note(f"submitted {handle.request_id}")
    note(f"follow it from another shell: generate_video.py --attach {handle.request_id}")

    try:
        result = follow(handle, note)
    except Exception as exc:
        return fail(f"generation failed: {exc}", request_id=handle.request_id)

    url = (result.get("video") or {}).get("url")
    if not url:
        return fail("fal returned no video url", result_keys=sorted(result))
    try:
        with urllib.request.urlopen(url, timeout=300) as res:
            out_mp4.write_bytes(res.read())
    except Exception as exc:
        return fail(f"could not download the result: {exc}", url=url)

    record = {
        "ok": True, "endpoint": endpoint, "source": a.source, "preprocess": preprocess,
        "frames_sent": len(shots), "request": {k: v for k, v in payload.items()
                    if k not in ("video_url", "first_frame_url", "ref_image_urls")},
        "reference_mode": a.reference_mode,
        "reference_frame": bool(reference),
        "video": out_mp4.name, "video_bytes": out_mp4.stat().st_size,
        "conditioning_sha256": hashlib.sha256(cond.read_bytes()).hexdigest()[:16],
        "wall_s": round(time.time() - t0, 1),
    }
    record_p.write_text(json.dumps(record, indent=2))
    print(json.dumps(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
