#!/usr/bin/env python3
"""The design stage: everything around the one judgement that is not judgement.

  design_product.py context  --workdir DIR    facts for the brief -> design/context.json
  design_product.py check    --workdir DIR    validate design/brief.json, price the plan -> design/plan.json
  design_product.py produce  --workdir DIR [--deadline S]   capture, generate -> design/produced.json, design/review.png
  design_product.py redo     --workdir DIR --reject id,id --reason "..."    set rejected paid assets aside; produce makes them again
  design_product.py finalize --workdir DIR [--reject id,id --reason "..."]  -> design/photos/, design/video.mp4, design.json

The skill (design-product) writes design/brief.json between `context` and
`check`, and chooses what to reject between `produce` and `finalize`. Every
other decision is here: angles and frame counts, what a lifestyle asset costs,
the spend cap, photo order, which clip becomes the listing video, Etsy's limits.

The listing standard (decided 2026-09-21) is config, not judgement: every
listing carries `lifestyle_images.count` lifestyle photos (4) and one lifestyle
clip (`lifestyle_video.required`). The brief must plan exactly that. Studio
stills are always captured, because every generated asset starts from one, but
they enter the listing only when the brief names them in `studio_photos`, or
when a lifestyle photo is missing and a still fills its place. A rejected or
failed paid asset is made again `redo_per_asset` times (1) inside the cap,
with the rejection in its prompt; the first attempt is kept as <asset>.rN.

Assets, all under DIR/design/:
  frames/stills/beauty/     the studio stills at the brief's yaws (browser studio)
  frames/turntable/beauty/  a full turn starting on the hero yaw -> turntable.mp4, only when fal could
                            not make the lifestyle clip (turntable.when "fallback"; "always" as before)
  frames/conditioning/      beauty and depth for the lifestyle clip
  lifestyle_N.png           image edits of our stills on fal       (paid)
  lifestyle.mp4             depth-conditioned Wan clip on fal      (paid)

Paid steps never run twice: each writes <asset>.request.json when fal accepts
it, a rerun attaches to that request, and spend is counted from those files
against `design.usd_cap_per_item`. Prices in config are estimates; fal does
not return a cost.

Config is the `design` block of store/config.json. Exit codes: 0 done, 1 failed
(one JSON line with `reason`), 2 usage or environment, 3 produce hit its
deadline with work left (run the same command again; nothing is repeated).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store_path import store_dir as _store_dir  # noqa: E402  one rule for every script

HERE = Path(__file__).resolve().parent
PY = sys.executable

DEFAULTS = {
    "studio_url": "http://localhost:8081",
    # The filaments the shop prints in, hex to the word a prompt uses. Store-specific: override in config.
    "palette": {"#E8E4DC": "warm white", "#1F1F1F": "matte black", "#3B7DD8": "blue", "#D8453B": "red",
                "#F2C230": "yellow", "#4E9A5B": "green", "#F08BB4": "pink", "#8A5CC7": "purple"},
    "stills": {"count": 4, "size": 2048, "elevation": 18,
               # without a studio page (an installed plugin has none) the stills come from Blender at this
               # size and sample count: one frame per call so a produce deadline never loses a finished frame
               "blender": {"res": 1536, "samples": 64}},
    # when "fallback" (decided 2026-09-22) the turntable is rendered only after fal failed to make the
    # lifestyle clip and its redo; "always" renders it on every item, as before. It is the listing video
    # only then, and on a server without a GPU it costs about half an hour of CPU.
    "turntable": {"frames": 240, "size": 1080, "fps": 24, "when": "fallback"},
    "lifestyle_images": {"count": 4, "endpoint": "fal-ai/nano-banana-pro/edit", "resolution": "2K",
                         "aspect": "1:1", "usd_each": 0.15},
    # engine i2v animates a lifestyle photo (the brief's start_from); engine vace paints over our depth pass.
    "lifestyle_video": {"enabled": True, "required": True, "engine": "i2v",
                        "i2v": {"endpoint": "minimax/h3-max/image-to-video", "resolution": "1080P", "seconds": 6,
                                "usd_per_second": 0.16},
                        "endpoint": "fal-ai/wan-22-vace-fun-a14b/depth", "frames": 120,
                        "fps": 24, "size": 720, "resolution": "720p", "steps": 50, "guidance": 5.0,
                        "usd_per_second": 0.10, "billed_fps": 16, "drift_arc": 40},
    "redo_per_asset": 1,
    "usd_cap_per_item": 2.0,
    "deadline_s": 480,
    "etsy": {"max_photos": 10, "photo_min_px": 2000, "video_min_s": 5.0, "video_max_s": 15.0, "video_max_mb": 100},
}
NAME_MAX = 60
SCENE_MAX = 600


# ------------------------------------------------------------------ plumbing

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def emit(obj: dict, code: int = 0) -> int:
    print(json.dumps(obj))
    return code


def fail(reason: str, code: int = 1, **extra) -> int:
    return emit({"ok": False, "reason": reason, **extra}, code)


def read(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def write(p: Path, obj: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    tmp.replace(p)


def merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_config() -> dict:
    store = _store_dir()
    for p in (store / "config.json", Path(__file__).resolve().parent.parent / "templates" / "config.example.json"):
        cfg = read(p)
        if cfg is not None:
            design = cfg.get("design", {})
            merged = merge(DEFAULTS, design)
            if "palette" in design:
                merged["palette"] = design["palette"]  # the shop's filaments replace the defaults outright
            return merged
    return dict(DEFAULTS)


def run_json(cmd: list[str], timeout: float | None = None) -> tuple[int, dict]:
    """Run a sibling script; its stdout is one JSON object. Progress passes through on stderr."""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=None, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, {"ok": False, "reason": f"{Path(cmd[1]).name} exceeded {timeout:.0f}s"}
    line = next((l for l in reversed(p.stdout.strip().splitlines()) if l.startswith("{")), "")
    try:
        return p.returncode, json.loads(line)
    except json.JSONDecodeError:
        return p.returncode or 1, {"ok": False, "reason": f"{Path(cmd[1]).name} printed no JSON: {p.stdout[-200:]}"}


def probe(path: Path) -> dict:
    """Width, height and duration from ffprobe; empty when it cannot tell."""
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height:format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True)
    try:
        j = json.loads(p.stdout)
        s = (j.get("streams") or [{}])[0]
        dur = j.get("format", {}).get("duration")
        return {"width": s.get("width"), "height": s.get("height"),
                "seconds": round(float(dur), 2) if dur not in (None, "N/A") else None}
    except (json.JSONDecodeError, ValueError):
        return {}


def scale_phrase(longest_mm: float) -> str:
    """How big the object is, in the words a scene prompt needs."""
    if longest_mm < 30:
        return "fingertip-sized"
    if longest_mm < 90:
        return "palm-sized"
    if longest_mm < 200:
        return "hand-held"
    if longest_mm < 400:
        return "tabletop-sized"
    return "large, floor or shelf-sized"


def image_count(cfg: dict) -> int:
    """Lifestyle photos every listing carries. `max` is the key's old name."""
    li = cfg["lifestyle_images"]
    return int(li.get("count") or li.get("max") or 0)


def clip_required(cfg: dict) -> bool:
    lv = cfg["lifestyle_video"]
    return bool(lv["enabled"] and lv.get("required"))


def video_usd(cfg: dict, frames: int | None = None, request: dict | None = None) -> float:
    """What one clip costs. `request` is a recorded fal request, so spend follows what was actually asked for."""
    lv = cfg["lifestyle_video"]
    if request is not None:
        if "duration" in request:  # an i2v request records seconds, a vace request frames
            return round(request["duration"] * lv["i2v"]["usd_per_second"], 2)
        frames = request.get("num_frames")
    elif lv.get("engine") == "i2v":
        return round(lv["i2v"]["seconds"] * lv["i2v"]["usd_per_second"], 2)
    return round((frames or lv["frames"]) / lv["billed_fps"] * lv["usd_per_second"], 2)


def assembly_facts(wd: Path) -> dict | None:
    doc = read(wd / "assembly.json")
    if not doc:
        return None
    return {"required_assembly": bool(doc.get("required_assembly")), "multicolour": bool(doc.get("multicolour")),
            "parts": [{"name": i.get("name"), "colour": i.get("colour")} for i in doc.get("items") or []],
            "note": "brief.assembled overrides required_assembly for this item's media; leave it out to follow the file"}


# ------------------------------------------------------------------ context

def _offer_facts(price: dict, viab: dict) -> dict:
    units = next((o["units"] for o in (price.get("offer"), viab.get("offer"))
                  if isinstance(o, dict) and isinstance(o.get("units"), int) and o["units"] > 0), 1)
    extras = (viab.get("offer") or {}).get("extras_common") or []
    return {"units": units, "extras_common": extras,
            "note": "one unit per sale" if units == 1 else
            f"the listing sells {units} of this product per purchase, as the market does; every lifestyle scene shows all {units}"
            + (f", and the comparables usually ship with a {', '.join(extras)}, which ours does not include" if extras else "")}


def cmd_context(a, cfg: dict) -> int:
    wd = Path(a.workdir).resolve()
    need = ["meta.json", "analysis.json", "price.json", "renders/draft_00.png"]
    missing = [n for n in need if not (wd / n).exists()]
    if missing:
        return fail(f"missing inputs: {missing}")
    meta, analysis, price = read(wd / "meta.json"), read(wd / "analysis.json"), read(wd / "price.json")
    rules = read(wd / "research" / "rules.json") or {}
    comps = read(wd / "comparables.json") or {}
    render = read(wd / "renders" / "render.json") or {}
    # An assembled display model is the product's real size; the analysis bbox is the print layout.
    shown = read(wd / "display.json") or {}
    bbox = (shown.get("bbox_mm") if shown.get("pose") == "assembled" else None) or analysis.get("bbox_mm") or [0, 0, 0]
    longest = max(bbox) if bbox else 0

    drafts = [{"file": f"renders/{o['file']}", "yaw": o["yaw"]} for o in render.get("draft", {}).get("outputs", [])]
    if not drafts:
        drafts = [{"file": f"renders/{p.name}", "yaw": 45.0 * i}
                  for i, p in enumerate(sorted((wd / "renders").glob("draft_*.png")))]

    li, lv = cfg["lifestyle_images"], cfg["lifestyle_video"]
    ctx = {
        "ok": True,
        "file": meta.get("file"),
        "product": {"noun": rules.get("noun") or comps.get("product"), "query": (comps.get("queries") or [None])[0],
                    "taxonomy_id": rules.get("taxonomy_id") or comps.get("taxonomy_id"), "notes": rules.get("notes"),
                    "identity": comps.get("identity") or rules.get("identity")},
        # The designer's own renders out of the 3MF, when the project carried them: in colour, as arranged on the bed.
        "thumbnails": [f"thumbnails/{p.name}" for p in sorted((wd / "thumbnails").glob("*.png"))],
        "comparable_titles": [c.get("title") for c in (comps.get("comparables") or [])[:8]],
        "price": {k: price.get(k) for k in ("price", "currency", "position")},
        # The unit of sale: how many of the product one purchase brings. A set is shown as a set in every
        # lifestyle scene; the studio stills show one unit. Decided 2026-10-12 after the kumiko coaster.
        "offer": _offer_facts(price, read(wd / "viability.json") or {}),
        "size": {"bbox_mm": [round(v) for v in bbox], "longest_cm": round(longest / 10, 1),
                 "scale": scale_phrase(longest), "bodies": analysis.get("bodies"),
                 "multi_part": bool((analysis.get("bodies") or 1) > 1 and (analysis.get("largest_body_frac") or 1) < 0.9)},
        # assembly_3mf.py: how a multi-part file goes together and the designer's filament colour per part.
        # When present the renders show display.ply: assembled when required_assembly, painted when multicolour.
        "assembly": assembly_facts(wd),
        "drafts": drafts,
        "draft_color": render.get("draft", {}).get("color"),
        "previous_choice": read(wd / "renders" / "choice.json"),
        "palette": cfg["palette"],
        "limits": {
            "name_max_chars": NAME_MAX,
            "angles": cfg["stills"]["count"] - 1,
            "lifestyle_images": image_count(cfg),
            "lifestyle_video": "required" if clip_required(cfg) else ("optional" if lv["enabled"] else "disabled"),
            "studio_photos": "optional extras, any of hero and angle_N",
            "redo_per_asset": cfg["redo_per_asset"],
            "scene_max_chars": SCENE_MAX,
            "elevation_range": [5, 60],
        },
        "costs_usd": {"lifestyle_image": li["usd_each"], "lifestyle_video": video_usd(cfg),
                      "cap_per_item": cfg["usd_cap_per_item"], "spent": spend(wd, cfg)["usd"]},
        "brief_path": "design/brief.json",
    }
    write(wd / "design" / "context.json", ctx)
    return emit({"ok": True, "context": "design/context.json", "drafts": len(drafts),
                 "scale": ctx["size"]["scale"], "noun": ctx["product"]["noun"], "costs_usd": ctx["costs_usd"]})


# ------------------------------------------------------------------ check

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def validate_brief(b: dict, cfg: dict) -> list[str]:
    e: list[str] = []
    allowed = {"name", "rotate", "hero_yaw", "angles", "elevation", "color", "lifestyle_images",
               "lifestyle_video", "studio_photos", "assembled", "reason"}
    if "assembled" in b and not isinstance(b["assembled"], bool):
        e.append("assembled: true or false, or leave it out to follow assembly.json")
    extra = set(b) - allowed
    if extra:
        e.append(f"unknown fields: {sorted(extra)}")
    name = b.get("name")
    if not isinstance(name, str) or not name.strip():
        e.append("name: required string")
    elif len(name) > NAME_MAX:
        e.append(f"name: {len(name)} chars, max {NAME_MAX}")
    elif any(ch in name for ch in "|#@*\n"):
        e.append("name: no | # @ * or line breaks")
    rot = b.get("rotate", "0,0,0")
    try:
        parts = [float(v) for v in str(rot).split(",")]
        if len(parts) != 3 or any(v % 90 for v in parts):
            raise ValueError
    except ValueError:
        e.append("rotate: three multiples of 90 as 'x,y,z'")
    yaw = b.get("hero_yaw")
    if not isinstance(yaw, (int, float)) or not 0 <= yaw < 360:
        e.append("hero_yaw: number in [0, 360)")
    angles = b.get("angles")
    want = cfg["stills"]["count"] - 1
    if not isinstance(angles, list) or len(angles) != want or not all(isinstance(v, (int, float)) for v in angles):
        e.append(f"angles: exactly {want} numbers")
    elif isinstance(yaw, (int, float)):
        seen = [yaw % 360] + [v % 360 for v in angles]
        for i, v in enumerate(seen):
            for w in seen[:i]:
                if min(abs(v - w), 360 - abs(v - w)) < 30:
                    e.append(f"angles: {v:g} is within 30 degrees of {w:g}; stills must show different sides")
    elev = b.get("elevation", cfg["stills"]["elevation"])
    if not isinstance(elev, (int, float)) or not 5 <= elev <= 60:
        e.append("elevation: number in [5, 60]")
    color = b.get("color")
    if not isinstance(color, str) or not HEX.match(color):
        e.append("color: '#RRGGBB'")
    elif color.upper() not in {c.upper() for c in cfg["palette"]}:  # keys of the palette
        e.append(f"color: {color} is not in the palette")
    refs = {"hero"} | {f"angle_{i + 1}" for i in range(want)}
    extras = b.get("studio_photos", [])
    if not isinstance(extras, list) or len(set(map(str, extras))) != len(extras) or any(x not in refs for x in extras):
        e.append(f"studio_photos: a list without repeats from {sorted(refs)}; [] for a listing of lifestyle photos only")
    li = b.get("lifestyle_images", [])
    if not isinstance(li, list) or len(li) != image_count(cfg):
        e.append(f"lifestyle_images: exactly {image_count(cfg)} scenes, the listing standard")
    else:
        for i, item in enumerate(li):
            if not isinstance(item, dict) or not isinstance(item.get("scene"), str) or not item["scene"].strip():
                e.append(f"lifestyle_images[{i}]: needs a scene")
                continue
            if len(item["scene"]) > SCENE_MAX:
                e.append(f"lifestyle_images[{i}].scene: max {SCENE_MAX} chars")
            if item.get("reference", "hero") not in refs:
                e.append(f"lifestyle_images[{i}].reference: one of {sorted(refs)}")
            if set(item) - {"scene", "reference"}:
                e.append(f"lifestyle_images[{i}]: only scene and reference")
    lv = b.get("lifestyle_video")
    if lv is None and clip_required(cfg):
        e.append("lifestyle_video: required, the listing standard; one scene and a motion")
    if lv is not None:
        if not cfg["lifestyle_video"]["enabled"]:
            e.append("lifestyle_video: disabled in config; set it to null")
        elif not isinstance(lv, dict) or not isinstance(lv.get("scene"), str) or not lv["scene"].strip():
            e.append("lifestyle_video: null or an object with a scene")
        else:
            if len(lv["scene"]) > SCENE_MAX:
                e.append(f"lifestyle_video.scene: max {SCENE_MAX} chars")
            if lv.get("motion", "drift") not in ("drift", "turn"):
                e.append("lifestyle_video.motion: drift or turn")
            if not isinstance(lv.get("negative", ""), str) or len(lv.get("negative", "")) > 500:
                e.append("lifestyle_video.negative: string, max 500 chars")
            if set(lv) - {"scene", "motion", "negative", "start_from"}:
                e.append("lifestyle_video: only scene, motion, negative and start_from")
            starts = {f"lifestyle_{i + 1}" for i in range(image_count(cfg))}
            if lv.get("start_from", "lifestyle_1") not in starts:
                e.append(f"lifestyle_video.start_from: one of {sorted(starts)}, the photo the clip opens on")
    if not isinstance(b.get("reason"), str) or not b["reason"].strip():
        e.append("reason: one or two sentences")
    return e


def plan_cost(b: dict, cfg: dict) -> dict:
    images = len(b.get("lifestyle_images") or [])
    video = 1 if b.get("lifestyle_video") else 0
    return {"images": images, "video": video,
            "usd": round(images * cfg["lifestyle_images"]["usd_each"] + video * video_usd(cfg), 2)}


def spend(wd: Path, cfg: dict) -> dict:
    """Committed spend: every paid asset whose request fal accepted, done or not."""
    items = []
    for req in sorted((wd / "design").glob("*.request.json")):  # a redone attempt keeps its request as <asset>.rN.*
        if req.name.startswith("lifestyle_") and ".png" in req.name:
            items.append({"asset": req.name.split(".")[0], "usd": cfg["lifestyle_images"]["usd_each"]})
        elif req.name.startswith("lifestyle.") and ".mp4" in req.name:
            r = read(req) or {}
            items.append({"asset": "lifestyle_video", "usd": video_usd(cfg, request=r.get("request") or {})})
    return {"usd": round(sum(i["usd"] for i in items), 2), "items": items,
            "basis": "estimated from config prices for every request fal accepted"}


def cmd_check(a, cfg: dict) -> int:
    wd = Path(a.workdir).resolve()
    b = read(wd / "design" / "brief.json")
    if b is None:
        return fail("design/brief.json is missing or not JSON")
    errors = validate_brief(b, cfg)
    cost = plan_cost(b, cfg)
    spent = spend(wd, cfg)
    # A committed request is one of the planned assets, so the plan's total is
    # the larger of the two, never their sum.
    total = round(max(cost["usd"], spent["usd"]), 2)
    if total > cfg["usd_cap_per_item"]:
        errors.append(f"plan costs about {total} USD against a cap of {cfg['usd_cap_per_item']}; the standard is "
                      "config, so a person raises design.usd_cap_per_item or lowers the standard")
    plan = {"ok": not errors, "errors": errors, "cost": cost, "committed_usd": spent["usd"],
            "total_usd": total, "cap_usd": cfg["usd_cap_per_item"], "checked": now()}
    write(wd / "design" / "plan.json", plan)
    return emit(plan, 0 if not errors else 1)


# ------------------------------------------------------------------ produce

def studio_up(url: str) -> bool:
    """True when the browser studio answers; an installed plugin has none and renders stills with Blender."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/", timeout=2) as r:
            return r.status < 500
    except Exception:
        return False


def blender_stills(wd: Path, b: dict, cfg: dict, yaws: list[float], elevation: str, stills: Path, left) -> tuple[int, dict]:
    """The studio stills from Blender, one frame per call so a finished frame survives the deadline.
    Returns (0, record) when every frame is there, (3, partial) at the deadline, (1, reason) on a failure."""
    beauty = stills / "beauty"
    beauty.mkdir(parents=True, exist_ok=True)
    bcfg = (cfg.get("stills") or {}).get("blender") or {}
    res, samples = int(bcfg.get("res", 1536)), int(bcfg.get("samples", 64))
    t0 = time.time()
    for i, yaw in enumerate(yaws):
        frame = beauty / f"frame_{i:04d}.png"
        if frame.exists():
            continue
        if left() < 120:
            return 3, {"partial": True, "frames_done": i, "frames": len(yaws)}
        tmp = stills / f"_blender_{i}"
        code, res_j = run_json([PY, str(HERE / "render_model.py"), "--workdir", str(wd), "--mode", "final",
                                "--angles", f"{yaw:g}", "--elevation", elevation, "--rotate", str(b.get("rotate", "0,0,0")),
                                "--color", b["color"], "--res", str(res), "--samples", str(samples), "--out", str(tmp)])
        hero = tmp / "hero.png"
        if code != 0 or not hero.exists():
            return 1, {"reason": f"Blender still {i} failed: {res_j.get('reason') or res_j}"}
        hero.replace(frame)
        shutil.rmtree(tmp, ignore_errors=True)
    record = {"renderer": "blender", "res": res, "samples": samples, "yaws": yaws, "elevation": float(elevation),
              "color": b["color"], "frames": len(yaws), "capture_s": round(time.time() - t0, 1)}
    (stills / "video.json").write_text(json.dumps(record, indent=2))
    return 0, record


def still_yaws(b: dict) -> list[float]:
    return [float(b["hero_yaw"]) % 360] + [float(v) % 360 for v in b["angles"]]


def frames_in(d: Path) -> int:
    return len(list(d.glob("frame_*.png"))) if d.exists() else 0


def cmd_produce(a, cfg: dict) -> int:
    wd = Path(a.workdir).resolve()
    dz = wd / "design"
    b = read(dz / "brief.json")
    plan = read(dz / "plan.json")
    if b is None or not plan or not plan.get("ok"):
        return fail("run check first: design/plan.json is missing or not ok")
    if validate_brief(b, cfg):
        return fail("design/brief.json changed since check; run check again")
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            return fail(f"{tool} not found", 2)

    deadline = a.deadline if a.deadline is not None else cfg["deadline_s"]
    t0 = time.time()
    left = lambda: deadline - (time.time() - t0)  # noqa: E731
    capture = [PY, str(HERE / "capture_turntable.py"), "--workdir", str(wd), "--studio", cfg["studio_url"],
               "--rotate", str(b.get("rotate", "0,0,0")), "--color", b["color"]]
    elevation = str(b.get("elevation", cfg["stills"]["elevation"]))
    steps: list[dict] = []
    produced = read(dz / "produced.json") or {"assets": {}}
    assets = produced["assets"]
    redone = read(dz / "redo.json") or {}

    def again(aid: str, prompt: str) -> tuple[str, int]:
        """The prompt and seed offset for an asset that was rejected before: say why, and draw differently."""
        r = redone.get(aid)
        if not r:
            return prompt, 0
        return (f"{prompt} An earlier attempt was rejected: {r['reasons'][-1]} "
                "Keep the product exactly as it is in the reference."), 100 * r["count"]

    def pending_exit(step: str) -> int:
        write(dz / "produced.json", {**produced, "updated": now()})
        return emit({"ok": False, "pending": True, "step": step, "steps": steps,
                     "reason": f"deadline of {deadline:g}s reached before {step}; run produce again"}, 3)

    # 0. the model the studio shows: parts placed as assembled or as printed, in their filament colours
    if (wd / "assembly.json").exists():
        cmd = [PY, str(HERE / "assembly_3mf.py"), "build", "--workdir", str(wd)]
        if isinstance(b.get("assembled"), bool):
            cmd += ["--pose", "assembled" if b["assembled"] else "as_printed"]
        code, res = run_json(cmd)
        if code != 0:
            return fail(f"display model failed: {res.get('reason')}", step="display")
        if produced.get("pose") not in (None, res.get("pose")):
            return fail("the pose changed after captures were made; a person clears design/frames to redo them", step="display")
        produced["pose"], produced["multicolour"] = res.get("pose"), res.get("multicolour")

    # 1. studio stills: the browser studio when it answers, else Blender (an installed plugin has no studio page)
    yaws = still_yaws(b)
    stills = dz / "frames" / "stills"
    studio = studio_up(cfg["studio_url"])
    if frames_in(stills / "beauty") != len(yaws) or not (stills / "video.json").exists():
        if left() < 60:
            return pending_exit("stills")
        if studio:
            code, res = run_json(capture + ["--yaws", ",".join(f"{y:g}" for y in yaws), "--size", str(cfg["stills"]["size"]),
                                            "--passes", "beauty", "--no-video", "--elevation", elevation,
                                            "--out", str(stills), "--timeout", str(max(left(), 60))])
            if code != 0:
                return fail(f"stills capture failed: {res.get('reason')}", step="stills")
            steps.append({"step": "stills", "renderer": "studio", "seconds": res.get("capture_s")})
        else:
            code, res = blender_stills(wd, b, cfg, yaws, elevation, stills, left)
            if code == 3:
                steps.append({"step": "stills", "renderer": "blender", **res})
                return pending_exit(f"stills ({res['frames_done']}/{res['frames']} frames, Blender)")
            if code != 0:
                return fail(res["reason"], step="stills")
            steps.append({"step": "stills", "renderer": "blender", "seconds": res.get("capture_s")})
    ids = ["hero"] + [f"angle_{i + 1}" for i in range(len(yaws) - 1)]
    for i, (sid, yaw) in enumerate(zip(ids, yaws)):
        assets[sid] = {"file": f"design/frames/stills/beauty/frame_{i:04d}.png", "kind": "studio", "yaw": yaw}

    # 3. lifestyle images, paid
    li = cfg["lifestyle_images"]
    for i, item in enumerate(b.get("lifestyle_images") or []):
        aid = f"lifestyle_{i + 1}"
        out = dz / f"{aid}.png"
        if not out.exists() and not (dz / f"{aid}.png.failed.json").exists():
            if left() < 30:
                return pending_exit(aid)
            ref = assets[item.get("reference", "hero")]["file"]
            # The chosen still first, then two more sides of the same object, so the model sees it in the round.
            others = [assets[k]["file"] for k in ids if assets[k]["file"] != ref][:2]
            prompt, seed = again(aid, item["scene"])
            code, res = run_json([PY, str(HERE / "generate_image.py"), "--reference", str(wd / ref),
                                  *[x for o in others for x in ("--reference", str(wd / o))],
                                  "--prompt", prompt, "--out", str(out), "--endpoint", li["endpoint"],
                                  "--aspect", li["aspect"], "--resolution", li["resolution"], "--seed", str(i + 1 + seed),
                                  "--deadline", str(max(left() - 10, 20))])
            if code == 3:
                return pending_exit(aid)
            if code == 2:
                return fail(f"{aid}: {res.get('reason')}", 2, step=aid)
            if code != 0:
                # A refused or failed generation is a fact about this asset, not the stage.
                write(dz / f"{aid}.png.failed.json", {"reason": res.get("reason"), "at": now()})
            steps.append({"step": aid, "ok": code == 0, "seconds": res.get("wall_s")})
        if out.exists():
            assets[aid] = {"file": f"design/{aid}.png", "kind": "lifestyle", "reference": item.get("reference", "hero")}
        else:
            assets[aid] = {"file": None, "kind": "lifestyle", "failed": (read(dz / f"{aid}.png.failed.json") or {}).get("reason")}

    # 4. lifestyle video, paid
    lvb, lv = b.get("lifestyle_video"), cfg["lifestyle_video"]
    if lvb and lv["enabled"] and lv.get("engine") == "i2v":
        out = dz / "lifestyle.mp4"
        failed = dz / "lifestyle.mp4.failed.json"
        start = assets.get(lvb.get("start_from", "lifestyle_1")) or {}
        if not out.exists() and not failed.exists():
            if not start.get("file"):
                write(failed, {"reason": f"{lvb.get('start_from', 'lifestyle_1')} was not produced, so the clip has no first frame", "at": now()})
            else:
                if left() < 30:
                    return pending_exit("lifestyle_video")
                prompt, seed = again("lifestyle_video", lvb["scene"])
                code, res = run_json([PY, str(HERE / "generate_video.py"), "--workdir", str(wd), "--engine", "i2v",
                                      "--endpoint", lv["i2v"]["endpoint"], "--image", str(wd / start["file"]),
                                      "--out", str(out), "--prompt", prompt, "--duration", str(lv["i2v"]["seconds"]),
                                      "--i2v-resolution", lv["i2v"]["resolution"], "--seed", str(1 + seed),
                                      "--deadline", str(max(left() - 10, 20))])
                if code == 3:
                    return pending_exit("lifestyle_video")
                if code == 2:
                    return fail(f"lifestyle_video: {res.get('reason')}", 2, step="lifestyle_video")
                if code != 0:
                    write(failed, {"reason": res.get("reason"), "at": now()})
                steps.append({"step": "lifestyle_video", "ok": code == 0, "seconds": res.get("wall_s")})
        if out.exists():
            assets["lifestyle_video"] = {"file": "design/lifestyle.mp4", "kind": "video", **probe(out),
                                         "bytes": out.stat().st_size, "start_from": lvb.get("start_from", "lifestyle_1")}
        else:
            assets["lifestyle_video"] = {"file": None, "kind": "video", "failed": (read(failed) or {}).get("reason")}
    elif lvb and lv["enabled"]:
        cond = dz / "frames" / "conditioning"
        out = dz / "lifestyle.mp4"
        failed = dz / "lifestyle.mp4.failed.json"
        if not out.exists() and not failed.exists():
            if frames_in(cond / "depth") != lv["frames"]:
                if left() < 120:
                    return pending_exit("conditioning")
                arc = 360 if lvb.get("motion") == "turn" else lv["drift_arc"]
                code, res = run_json(capture + ["--frames", str(lv["frames"]), "--size", str(lv["size"]),
                                                "--fps", str(lv["fps"]), "--passes", "beauty,depth",
                                                "--start-yaw", f"{yaws[0]:g}", "--arc", str(arc), "--no-video",
                                                "--elevation", elevation, "--out", str(cond),
                                                "--timeout", str(max(left(), 120))])
                if code != 0:
                    return fail(f"conditioning capture failed: {res.get('reason')}", step="conditioning")
                steps.append({"step": "conditioning", "seconds": res.get("capture_s")})
            if left() < 30:
                return pending_exit("lifestyle_video")
            prompt, seed = again("lifestyle_video", lvb["scene"])
            cmd = [PY, str(HERE / "generate_video.py"), "--workdir", str(wd), "--source", "depth",
                   "--frames-root", str(cond), "--out", str(out), "--prompt", prompt, "--seed", str(1 + seed),
                   "--frames", str(lv["frames"]), "--fps", str(lv["fps"]), "--resolution", lv["resolution"],
                   "--steps", str(lv["steps"]), "--guidance", str(lv["guidance"]), "--endpoint", lv["endpoint"],
                   "--deadline", str(max(left() - 10, 20))]
            if lvb.get("negative"):
                cmd += ["--negative", lvb["negative"]]
            code, res = run_json(cmd)
            if code == 3:
                return pending_exit("lifestyle_video")
            if code == 2:
                return fail(f"lifestyle_video: {res.get('reason')}", 2, step="lifestyle_video")
            if code != 0:
                write(failed, {"reason": res.get("reason"), "at": now()})
            steps.append({"step": "lifestyle_video", "ok": code == 0, "seconds": res.get("wall_s")})
        if out.exists():
            assets["lifestyle_video"] = {"file": "design/lifestyle.mp4", "kind": "video", **probe(out),
                                         "bytes": out.stat().st_size}
        else:
            assets["lifestyle_video"] = {"file": None, "kind": "video", "failed": (read(failed) or {}).get("reason")}

    # 5. studio turntable, starting on the hero. Only when fal could not make the lifestyle clip (it failed,
    # and its redo failed too or the cap has no room for one), or when config says always. A clip the
    # reviewer rejected is not a fal error and gets no turntable: the listing then goes without a video.
    tt = dz / "frames" / "turntable"
    tt_mp4 = dz / "turntable.mp4"
    clip = assets.get("lifestyle_video") or {}
    clip_redos = redone.get("lifestyle_video", {}).get("count", 0)
    no_room = cfg["usd_cap_per_item"] - spend(wd, cfg)["usd"] < video_usd(cfg) - 1e-9
    fal_gave_up = bool(clip.get("failed")) and (clip_redos >= cfg["redo_per_asset"] or no_room)
    planned = bool(lvb and lv["enabled"])
    if not studio and not tt_mp4.exists() and (cfg["turntable"].get("when") == "always" or fal_gave_up or not planned):
        steps.append({"step": "turntable", "skipped": "no studio page; the turntable needs the browser studio, so the listing has no video"})
    elif tt_mp4.exists() or cfg["turntable"].get("when") == "always" or fal_gave_up or not planned:
        if not tt_mp4.exists():
            if left() < 120:
                return pending_exit("turntable")
            t = cfg["turntable"]
            code, res = run_json(capture + ["--frames", str(t["frames"]), "--size", str(t["size"]), "--fps", str(t["fps"]),
                                            "--passes", "beauty", "--start-yaw", f"{(yaws[0] + 180) % 360:g}", "--arc", "360",
                                            "--elevation", elevation, "--out", str(tt), "--resume",
                                            "--timeout", str(max(left() - 30, 90))])
            if code == 3 and res.get("partial"):   # a slow machine (software WebGL on a server): frames kept, next call goes on
                steps.append({"step": "turntable", "frames_done": res.get("frames_done"), "frames": res.get("frames")})
                return pending_exit(f"turntable ({res.get('frames_done')}/{res.get('frames')} frames)")
            if code != 0:
                return fail(f"turntable capture failed: {res.get('reason')}", step="turntable")
            (tt / "turntable.mp4").replace(tt_mp4)
            steps.append({"step": "turntable", "seconds": res.get("capture_s")})
        assets["turntable"] = {"file": "design/turntable.mp4", "kind": "video", **probe(tt_mp4),
                               "bytes": tt_mp4.stat().st_size}

    # 6. one review sheet: every photo, and four frames of every clip
    order = review_sheet(dz, assets)
    produced = {"ok": True, "pose": produced.get("pose"), "multicolour": produced.get("multicolour"),
                "assets": assets, "review": "design/review.png", "review_cells": order,
                "spend": spend(wd, cfg), "updated": now()}
    write(dz / "produced.json", produced)
    return emit({"ok": True, "steps": steps, "review": "design/review.png", "review_cells": order,
                 "failed": [k for k, v in assets.items() if v.get("failed")], "spend_usd": produced["spend"]["usd"]})


def review_sheet(dz: Path, assets: dict) -> list[str]:
    """A grid at 384 px a cell, row-major, so one Read shows everything. Returns cell labels in order."""
    cells: list[tuple[str, Path]] = []
    rev = dz / "frames" / "review"
    rev.mkdir(parents=True, exist_ok=True)
    for aid, asset in assets.items():
        if not asset.get("file"):
            continue
        src = dz.parent / asset["file"]
        if asset["kind"] != "video":
            cells.append((aid, src))
            continue
        dur = asset.get("seconds") or 5
        for k in range(4):
            frame = rev / f"{aid}_{k}.png"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{dur * (k + 0.5) / 4:.2f}", "-i", str(src),
                            "-frames:v", "1", str(frame)], capture_output=True)
            if frame.exists():
                cells.append((f"{aid}@{k + 1}/4", frame))
    if not cells:
        return []
    cols = 4
    inputs: list[str] = []
    chains = []
    for i, (_, path) in enumerate(cells):
        inputs += ["-i", str(path)]
        chains.append(f"[{i}:v]scale=384:384:force_original_aspect_ratio=decrease,"
                      f"pad=384:384:(ow-iw)/2:(oh-ih)/2:color=white,setsar=1[c{i}]")
    n = len(cells)
    if n == 1:
        graph = chains[0] + ";[c0]null[out]"
    else:
        layout = "|".join(f"{(i % cols) * 384}_{(i // cols) * 384}" for i in range(n))
        graph = ";".join(chains) + ";" + "".join(f"[c{i}]" for i in range(n)) + \
            f"xstack=inputs={n}:layout={layout}:fill=white[out]"
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[out]",
                    "-frames:v", "1", str(dz / "review.png")], capture_output=True)
    return [label for label, _ in cells]


# ------------------------------------------------------------------ redo

def cmd_redo(a, cfg: dict) -> int:
    """Set rejected paid assets aside so the next produce makes them again. Nothing is deleted."""
    wd = Path(a.workdir).resolve()
    dz = wd / "design"
    if (wd / "design.json").exists():
        return fail("design.json exists; the stage is finished")
    produced = read(dz / "produced.json")
    if not produced or not produced.get("ok"):
        return fail("run produce first: design/produced.json is missing or not ok")
    ids = [r.strip() for r in (a.reject or "").split(",") if r.strip()]
    paid = [k for k, v in produced["assets"].items() if v["kind"] == "lifestyle" or k == "lifestyle_video"]
    if not ids or any(i not in paid for i in ids):
        return fail(f"--reject takes paid assets only: {sorted(paid)}")
    if not (a.reason or "").strip():
        return fail("--reason is required: it goes into the next attempt's prompt")

    redone = read(dz / "redo.json") or {}
    budget = cfg["usd_cap_per_item"] - spend(wd, cfg)["usd"]
    accepted, used_up, over_cap = [], [], []
    for aid in sorted(ids, key=lambda i: i == "lifestyle_video"):  # photos first: they are the cheaper fix
        cost = video_usd(cfg) if aid == "lifestyle_video" else cfg["lifestyle_images"]["usd_each"]
        if redone.get(aid, {}).get("count", 0) >= cfg["redo_per_asset"]:
            used_up.append(aid)
        elif cost > budget + 1e-9:
            over_cap.append(aid)
        else:
            budget -= cost
            accepted.append(aid)
    for aid in accepted:
        entry = redone.setdefault(aid, {"count": 0, "reasons": []})
        entry["count"] += 1
        entry["reasons"].append(a.reason.strip())
        stem, ext = ("lifestyle", ".mp4") if aid == "lifestyle_video" else (aid, ".png")
        for f in sorted(dz.glob(f"{stem}{ext}*")):  # the output, its record, its request, a failure note
            f.rename(f.with_name(f"{stem}.r{entry['count']}{f.name[len(stem):]}"))
    write(dz / "redo.json", redone)
    if accepted:
        write(dz / "produced.json", {**produced, "ok": False, "redo_pending": accepted, "updated": now()})
    return emit({"ok": True, "redo": accepted, "already_redone": used_up, "over_cap": over_cap,
                 "next": "run produce again, review, then finalize; reject there whatever is in already_redone "
                         "or over_cap" if accepted else "nothing to redo; finalize with --reject"})


# ------------------------------------------------------------------ finalize

def image_size(path: Path) -> tuple[int | None, int | None]:
    info = probe(path)
    return info.get("width"), info.get("height")


def cmd_finalize(a, cfg: dict) -> int:
    wd = Path(a.workdir).resolve()
    dz = wd / "design"
    if (wd / "design.json").exists():
        return fail("design.json exists; a person deletes it to redo the stage")
    b, produced = read(dz / "brief.json"), read(dz / "produced.json")
    if not b or not produced or not produced.get("ok"):
        return fail("run produce first: design/produced.json is missing or not ok")
    assets = produced["assets"]
    rejected_ids = [r.strip() for r in (a.reject or "").split(",") if r.strip()]
    unknown = [r for r in rejected_ids if r not in assets]
    if unknown:
        return fail(f"unknown asset ids to reject: {unknown}; known: {sorted(assets)}")
    if "hero" in rejected_ids or "turntable" in rejected_ids:
        return fail("hero and turntable cannot be rejected; a bad hero means the stage failed")
    if rejected_ids and not (a.reason or "").strip():
        return fail("--reason is required with --reject")

    redo_left = [r for r in rejected_ids + [k for k, v in assets.items() if v.get("failed")]
                 if (assets[r]["kind"] == "lifestyle" or r == "lifestyle_video")
                 and (read(dz / "redo.json") or {}).get(r, {}).get("count", 0) < cfg["redo_per_asset"]]
    if redo_left and not a.no_redo:
        return fail(f"{redo_left} can still be made again: run redo --reject {','.join(redo_left)} --reason \"...\", "
                    "then produce; pass --no-redo only when redo answered over_cap")

    etsy = cfg["etsy"]
    warnings: list[str] = []
    # The listing is lifestyle photos; a studio still enters it when the brief asked for it, or to stand in
    # for a lifestyle photo that was rejected or failed, so a listing never goes short of pictures.
    lifestyle_ids = [k for k in assets if assets[k]["kind"] == "lifestyle"]
    kept = [k for k in lifestyle_ids if k not in rejected_ids and assets[k].get("file")]
    stills = ["hero"] + sorted(k for k in assets if k.startswith("angle_"))
    extras = [k for k in (b.get("studio_photos") or []) if k in assets]
    short = max(image_count(cfg) - len(kept), 0)
    fill = [k for k in stills if k not in extras and k not in rejected_ids][:short]
    if short:
        warnings.append(f"{len(kept)} of {image_count(cfg)} lifestyle photos kept; studio stills {fill} stand in")
    order = kept + fill + extras
    photos_dir = dz / "photos"
    if photos_dir.exists():
        for stale in photos_dir.glob("*.png"):  # finalize owns this folder until design.json exists
            stale.unlink()
    photos = []
    for aid in order:
        asset = assets[aid]
        if aid in rejected_ids or not asset.get("file"):
            continue
        if len(photos) >= etsy["max_photos"]:
            warnings.append(f"{aid} dropped: Etsy takes {etsy['max_photos']} photos")
            continue
        src = wd / asset["file"]
        dst = photos_dir / f"{len(photos) + 1:02d}_{aid}.png"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        w, h = image_size(dst)
        if w and h and min(w, h) < etsy["photo_min_px"]:
            warnings.append(f"{aid} is {w}x{h}, under Etsy's {etsy['photo_min_px']} px recommendation")
        photos.append({"id": aid, "file": str(dst.relative_to(wd)), "kind": asset["kind"],
                       "yaw": asset.get("yaw"), "px": [w, h]})

    # The size card: a straight-down still with the true dimensions drawn on it. No model made it, so it is
    # never rejected; it goes last. Built by hand for now (out of display.json), wired in when it has a script.
    card = dz / "size_card.png"
    if card.exists() and len(photos) < etsy["max_photos"]:
        dst = photos_dir / f"{len(photos) + 1:02d}_size_card.png"
        shutil.copy2(card, dst)
        w, h = image_size(dst)
        photos.append({"id": "size_card", "file": str(dst.relative_to(wd)), "kind": "size_card", "yaw": None, "px": [w, h]})

    def fits(v: dict | None) -> str | None:
        if not v or not v.get("file"):
            return "not produced"
        s, mb = v.get("seconds"), (v.get("bytes") or 0) / 1e6
        if s is None or s < etsy["video_min_s"] - 0.05 or s > etsy["video_max_s"] + 0.05:
            return f"{s} s is outside Etsy's {etsy['video_min_s']:g} to {etsy['video_max_s']:g} s"
        if mb > etsy["video_max_mb"]:
            return f"{mb:.0f} MB is over Etsy's {etsy['video_max_mb']} MB"
        return None

    lifestyle = assets.get("lifestyle_video")
    candidates = []
    if lifestyle and "lifestyle_video" not in rejected_ids:
        candidates.append(("lifestyle_video", lifestyle))
    if assets.get("turntable", {}).get("file"):
        candidates.append(("turntable", assets["turntable"]))
    video = None
    for vid, v in candidates:
        why = fits(v)
        if why is None:
            video = {"id": vid, "source": v["file"], "seconds": v.get("seconds"), "bytes": v.get("bytes")}
            break
        warnings.append(f"{vid} not used as the listing video: {why}")
    # A video is not required by Etsy. With the turntable only a fallback for a fal failure, a clip the
    # reviewer rejected leaves the listing without one, which is a result, not a failure.
    if video is None:
        why = ("the lifestyle clip was rejected, and a rejection gets no turntable" if "lifestyle_video" in rejected_ids
               else "; ".join(w for w in warnings if "listing video" in w) or "none produced")
        warnings.append(f"no listing video: {why}")
    else:
        shutil.copy2(wd / video["source"], dz / "video.mp4")
        video["file"] = "design/video.mp4"

    redone = read(dz / "redo.json") or {}
    standard = {"lifestyle_images": {"want": image_count(cfg), "kept": len(kept)},
                "lifestyle_video": {"want": clip_required(cfg), "kept": bool(video) and video["id"] == "lifestyle_video"}}
    standard["met"] = not short and (standard["lifestyle_video"]["kept"] or not clip_required(cfg))
    manifest = {
        "ok": True, "name": b["name"], "color": b["color"], "rotate": b.get("rotate", "0,0,0"),
        "hero_yaw": b["hero_yaw"], "reason": b["reason"],
        "pose": produced.get("pose"), "multicolour": produced.get("multicolour"),
        "photos": photos, "video": video, "standard": standard,
        "redone": [{"id": k, **v} for k, v in redone.items()],
        "videos": {k: assets[k]["file"] for k in ("turntable", "lifestyle_video") if assets.get(k, {}).get("file")},
        "rejected": [{"id": r, "reason": a.reason} for r in rejected_ids],
        "failed": [{"id": k, "reason": v["failed"]} for k, v in assets.items() if v.get("failed")],
        "spend": spend(wd, cfg), "warnings": warnings, "finished": now(),
    }
    write(wd / "design.json", manifest)
    return emit({"ok": True, "design": "design.json", "name": b["name"], "photos": len(photos),
                 "video": video["id"] if video else None, "standard_met": standard["met"], "spend_usd": manifest["spend"]["usd"],
                 "warnings": warnings})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("context", "check", "produce", "redo", "finalize"):
        p = sub.add_parser(name)
        p.add_argument("--workdir", required=True)
        if name == "produce":
            p.add_argument("--deadline", type=float, default=None, help="seconds before exiting 3; default design.deadline_s")
        if name in ("redo", "finalize"):
            p.add_argument("--reject", default=None, help="asset ids from design/produced.json, comma-separated")
            p.add_argument("--reason", default=None)
        if name == "finalize":
            p.add_argument("--no-redo", action="store_true", help="finalize although a rejected asset has a redo left")
    a = ap.parse_args()
    if not Path(a.workdir).is_dir():
        return fail(f"no work dir at {a.workdir}", 2)
    cfg = load_config()
    return {"context": cmd_context, "check": cmd_check, "produce": cmd_produce, "redo": cmd_redo,
            "finalize": cmd_finalize}[a.cmd](a, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
