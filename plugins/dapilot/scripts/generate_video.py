#!/usr/bin/env python3
"""Turn a captured turntable into a photoreal clip with Wan, on fal.

  generate_video.py --workdir DIR --source depth  [--prompt "..."] [--frames 120]
  generate_video.py --workdir DIR --source beauty [--prompt "..."]
  generate_video.py --workdir DIR --engine ref --videos a.mp4 b.mp4 [--prompt "..."]
  generate_video.py --workdir DIR --engine i2v --image photo.png --prompt "..." [--duration 5]

--engine i2v     animates one still, normally a lifestyle photo that already passed
                 review, so the clip opens on a picture the listing also shows. No
                 structural control: whoever reviews it watches for the product
                 changing shape. The audio track the model adds is removed.

--source depth   sends our ground-truth depth sequence with preprocess off, so
                 the model is conditioned on the real geometry of the mesh.
--source beauty  sends the plain turntable with preprocess on, so fal estimates
                 depth itself. The fallback, and the control in a comparison.

Frames are read from FRAMES_ROOT/<source>/ (default DIR/renders, where
capture_turntable.py writes them). Writes OUT (default DIR/renders/<name>.mp4),
OUT.request.json the moment fal accepts the request, and a record at
<OUT stem>.json. Prints one JSON object.

Exit 0 done, 1 failed, 2 usage or environment, 3 still running on fal after
--deadline seconds. A rerun with the request file present attaches to that
request; it never submits a second generation unless --force.

Needs FAL_KEY or FAL_API_KEY (store/.env), ffmpeg, and a captured sequence from
capture_turntable.py. Spends money: every submitted run is a real generation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fal_queue  # noqa: E402

ENDPOINTS = {
    # Depth-conditioned. Geometry is locked per frame; caps at 720p.
    "vace": "fal-ai/wan-22-vace-fun-a14b/depth",
    # Reference-to-video. No structural control, but 1080p and it accepts up to
    # three reference clips, which a full turntable suits unusually well.
    "ref": "wan/v2.6/reference-to-video",
    # Image-to-video from a first frame. FLUX 3 Max since 2026-10-23: a 10 s clip in about 11 s,
    # 5 to 20 s, 720p or 1080p, silent on request. minimax/h3-max/image-to-video (the engine from
    # 2026-09-21 to 2026-10-23) still works through --endpoint; i2v_payload knows both shapes.
    "i2v": "blackforestlabs/flux-3-max/image-to-video",
}

I2V_RESOLUTIONS = ["480P", "768P", "720p", "1080P", "1080p"]


def i2v_payload(endpoint: str, prompt: str, image_url: str, duration: int, resolution: str,
                aspect: str, seed: int) -> dict:
    """The image-to-video request for one endpoint family. FLUX 3 Max names resolution in lower
    case, follows the first frame's aspect on "auto", generates audio unless told not to and has no
    prompt expansion to switch off; MiniMax H3 Max takes 480P/768P/1080P and a prompt_expansion_mode."""
    base = {"prompt": prompt, "image_url": image_url, "duration": duration, "seed": seed}
    if "flux-3-max" in endpoint:
        res = "1080p" if resolution.lower() == "1080p" else "720p"
        return {**base, "resolution": res, "aspect_ratio": aspect if aspect != "auto" else "auto",
                # Listing clips are silent; our own wording is the shot, so the slower prompt reasoning stays on.
                "generate_audio": False, "reasoning_level": "high"}
    res = resolution.upper()
    if res == "720P":
        res = "768P"
    return {**base, "resolution": res,
            # Our own wording, not a rewrite: the scene text names what must not change.
            "prompt_expansion_mode": "disabled"}

DEFAULT_PROMPT = (
    "a product photograph of a matte 3D printed object on a soft studio backdrop, "
    "even diffused lighting, shallow depth of field, high detail, photographic"
)
NEGATIVE = "cartoon, illustration, distorted geometry, extra objects, text, watermark"


def fail(reason: str, code: int = 1, **extra) -> int:
    print(json.dumps({"ok": False, "reason": reason, **extra}))
    return code


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
    ap.add_argument("--duration", type=int, default=5, help="seconds: 5 or 10 for ref, 5 to 20 for i2v (15 on MiniMax)")
    ap.add_argument("--image", metavar="PNG", help="i2v engine: the still the clip starts from")
    ap.add_argument("--i2v-resolution", default="1080p", choices=I2V_RESOLUTIONS)
    ap.add_argument("--source", choices=["depth", "beauty"], default="depth")
    ap.add_argument("--frames-root", default=None, help="directory holding <source>/frame_*.png; default WORKDIR/renders")
    ap.add_argument("--out", default=None, help="output mp4; default WORKDIR/renders/<name>.mp4")
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
                         "Defaults to FRAMES_ROOT/beauty/frame_0000.png when it exists.")
    ap.add_argument("--no-reference", action="store_true", help="send depth alone")
    ap.add_argument("--reference-mode", choices=["ref", "first"], default="ref",
                    help="ref guides appearance only; first pins frame 0 to the image. "
                         "Use ref when the prompt changes the setting, or frame 0 will "
                         "be our studio sweep and the clip will morph out of it.")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--name", default=None, help="output stem; defaults to lifestyle_<source>")
    ap.add_argument("--deadline", type=float, default=0, help="seconds to wait before exiting 3; 0 waits")
    ap.add_argument("--force", action="store_true", help="submit again even if an output or request exists")
    ap.add_argument("--attach", metavar="REQUEST_ID",
                    help="wait on a request already in flight and save it to the output")
    a = ap.parse_args()

    wd = Path(a.workdir).resolve()
    frames_root = Path(a.frames_root).resolve() if a.frames_root else wd / "renders"
    endpoint = a.endpoint or ENDPOINTS[a.engine]
    default_name = "reference_video" if a.engine == "ref" else f"lifestyle_{a.source}"
    out_mp4 = Path(a.out).resolve() if a.out else wd / "renders" / f"{a.name or default_name}.mp4"
    req_p = out_mp4.with_name(out_mp4.name + ".request.json")
    record_p = out_mp4.with_suffix(".json")

    if out_mp4.exists() and not a.force:
        print(json.dumps({"ok": True, "cached": True, "video": str(out_mp4)}))
        return 0

    k = fal_queue.key()
    if not k:
        return fail("neither FAL_KEY nor FAL_API_KEY is set; put one in store/.env and source it", 2)
    problem = fal_queue.preflight(k)
    if problem:
        return fail(problem, 2)
    if not shutil.which("ffmpeg"):
        return fail("ffmpeg not found", 2)

    import fal_client                                  # imported late: no key, no need

    t0 = time.time()
    extra: dict = {}
    held = None if a.force else fal_queue.pending(req_p)
    if a.attach:
        held = {"request_id": a.attach, "endpoint": endpoint, "request": {}}
    if held:
        endpoint, request_id, recorded = held["endpoint"], held["request_id"], held.get("request", {})
        fal_queue.note(f"attaching to {request_id}" + (f", submitted {held['submitted']}" if held.get("submitted") else ""))
    elif a.engine == "i2v":
        i2v_max = 20 if "flux-3-max" in endpoint else 15
        if not 5 <= a.duration <= i2v_max:
            return fail(f"the i2v engine takes 5 to {i2v_max} seconds", 2)
        still = Path(a.image) if a.image else None
        if not still or not still.exists():
            return fail("--image is required for the i2v engine: the still the clip starts from", 2)
        try:
            image_url = fal_client.upload_file(str(still))
        except Exception as exc:
            return fail(f"uploading {still.name} failed: {exc}")
        payload = i2v_payload(endpoint, a.prompt, image_url, a.duration, a.i2v_resolution, a.aspect, a.seed)
        recorded = {**{k2: v for k2, v in payload.items() if k2 != "image_url"}, "image": still.name}
        try:
            request_id = fal_queue.submit(endpoint, payload, req_p, recorded)
        except Exception as exc:
            return fail(f"submit failed: {exc}", endpoint=endpoint)
        fal_queue.note(f"submitted {request_id}")
        extra = {"engine": "i2v", "image": still.name}
    elif a.engine == "ref":
        if a.duration not in (5, 10):
            return fail("the ref engine takes 5 or 10 seconds", 2)
        clips = [Path(v) for v in (a.videos or [])]
        if not clips:
            return fail("--videos is required for the ref engine (1 to 3 clips)", 2)
        if len(clips) > 3:
            return fail("the ref engine takes at most 3 videos", 2)
        for c in clips:
            if not c.exists():
                return fail(f"no video at {c}", 2)
            if c.stat().st_size > 30 * 1024 * 1024:
                return fail(f"{c.name} is over the 30 MB per-clip limit", 2)
        urls = []
        for c in clips:
            try:
                urls.append(fal_client.upload_file(str(c)))
            except Exception as exc:
                return fail(f"uploading {c.name} failed: {exc}")
            fal_queue.note(f"uploaded {c.name} as @Video{len(urls)}")
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
        recorded = {**{k2: v for k2, v in payload.items() if k2 != "video_urls"}, "clips": [c.name for c in clips]}
        try:
            request_id = fal_queue.submit(endpoint, payload, req_p, recorded)
        except Exception as exc:
            return fail(f"submit failed: {exc}", endpoint=endpoint)
        fal_queue.note(f"submitted {request_id}")
        extra = {"engine": "ref", "clips": [c.name for c in clips]}
    else:
        frames_dir = frames_root / a.source
        shots = sorted(frames_dir.glob("frame_*.png"))
        if not shots:
            return fail(f"no {a.source} frames in {frames_dir}; run capture_turntable.py first", 2)

        # preprocess off means "this video IS the control signal"; on means
        # "estimate depth from this video first". That single flag is the whole
        # difference between our geometry and a guess at it.
        preprocess = a.source == "beauty"
        cond = out_mp4.with_name(f"_conditioning_{a.source}.mp4")
        cond.parent.mkdir(parents=True, exist_ok=True)
        ok, why = encode_conditioning(frames_dir, cond, a.fps)
        if not ok:
            return fail(f"ffmpeg failed encoding the conditioning video: {why}")
        try:
            video_url = fal_client.upload_file(str(cond))
        except Exception as exc:
            return fail(f"upload to fal failed: {exc}")

        reference = None
        if not a.no_reference:
            chosen = Path(a.reference) if a.reference else (frames_root / "beauty" / "frame_0000.png")
            if chosen.exists():
                try:
                    reference = fal_client.upload_file(str(chosen))
                except Exception as exc:
                    return fail(f"uploading the reference frame failed: {exc}")
                fal_queue.note(f"reference frame: {chosen.name}")
            elif a.reference:
                return fail(f"no reference frame at {chosen}", 2)
            else:
                fal_queue.note("no reference frame found; depth alone")

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
        recorded = {k2: v for k2, v in payload.items() if k2 not in ("video_url", "first_frame_url", "ref_image_urls")}
        recorded.update(source=a.source, reference_mode=a.reference_mode, reference_frame=bool(reference),
                        frames_sent=len(shots),
                        conditioning_sha256=hashlib.sha256(cond.read_bytes()).hexdigest()[:16])
        try:
            request_id = fal_queue.submit(endpoint, payload, req_p, recorded)
        except Exception as exc:
            return fail(f"submit failed: {exc}", sent=recorded)
        fal_queue.note(f"submitted {request_id}; a rerun of this command attaches to it")

    try:
        result = fal_queue.wait(endpoint, request_id, a.deadline, fal_queue.note)
    except Exception as exc:
        return fail(f"generation failed: {exc}", request_id=request_id)
    if result is None:
        print(json.dumps({"ok": False, "pending": True, "request_id": request_id,
                          "reason": f"still running on fal after {a.deadline:g}s; run the same command again"}))
        return fal_queue.PENDING_EXIT

    url = (result.get("video") or {}).get("url")
    if not url:
        return fail("fal returned no video url", result_keys=sorted(result), request_id=request_id)
    try:
        fal_queue.download(url, out_mp4)
    except Exception as exc:
        return fail(f"could not download the result: {exc}", url=url, request_id=request_id)

    if extra.get("engine") == "i2v" or (held and "image-to-video" in endpoint):
        # Listing videos are silent (Etsy drops audio anyway); keep the picture stream untouched.
        silent = out_mp4.with_name(out_mp4.stem + ".silent.mp4")
        p = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(out_mp4), "-an", "-c:v", "copy", str(silent)],
                           capture_output=True, text=True)
        if p.returncode == 0:
            silent.replace(out_mp4)

    record = {"ok": True, "endpoint": endpoint, "request_id": request_id, **extra,
              "request": recorded, "video": out_mp4.name, "video_bytes": out_mp4.stat().st_size,
              "wall_s": round(time.time() - t0, 1)}
    record_p.write_text(json.dumps(record, indent=2))
    print(json.dumps(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
