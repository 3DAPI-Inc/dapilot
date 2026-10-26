#!/usr/bin/env python3
"""The preview stage: eight draft frames of one model, so later stages can see what the object is.

  preview_model.py --workdir DIR [--frames 8] [--size 512]

Runs capture_turntable.py --mode draft on the work dir's source (display.ply when assembly_3mf.py
built one), which writes DIR/renders/draft_00.png onward and a `draft` record into
DIR/renders/render.json. Prints one JSON object. Exit 0 when the drafts exist, 1 otherwise.

This is the stage's script, named in stages.json, so run_intake.py runs it directly at no model
cost: there is no judgement in a preview. By hand it is the same command. Cached when draft_00.png
already exists; the same upload is never rendered twice.

Replaced render_model.py --mode draft (Blender) on 2026-10-20. The frames come from the WebGL
studio in headless Chrome, served by the capture script from the plugin's own built page when no
console is running.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--size", type=int, default=512)
    # 240 s until 2026-10-23: a hosted run's first attempt timed out at 240 s and the second took 223 s, headless
    # Chrome on a CPU with no GPU being that slow. The worker wall clock (max_worker_minutes, 15) is the outer bound.
    ap.add_argument("--timeout", type=float, default=600.0, help="seconds for the whole capture")
    a = ap.parse_args()
    wd = Path(a.workdir).resolve()
    if not (wd.is_dir() and any(p.is_file() for p in wd.glob("source.*"))):
        print(json.dumps({"ok": False, "reason": f"no source.<ext> in {wd}"}))
        return 1
    first = wd / "renders" / "draft_00.png"
    renders = wd / "renders"
    drafts = sorted(renders.glob("draft_*.png"))
    record = renders / "render.json"
    # Whole, or nothing (2026-10-26): the seller's browser may have drawn the drafts at upload and written
    # render.json last. Eight non-empty frames and the record are a finished set, from whichever renderer;
    # fewer, or an empty one, is an upload that stopped, so the frames go and this script draws them.
    whole = len(drafts) >= a.frames and all(p.stat().st_size > 0 for p in drafts[:a.frames]) and record.exists()
    if whole:
        try:
            who = (json.loads(record.read_text()).get("draft") or {}).get("renderer")
        except (OSError, ValueError):
            who = None
        print(json.dumps({"ok": True, "cached": True, "outputs": ["renders/draft_00.png", "renders/render.json"], "renderer": who}))
        return 0
    if drafts or record.exists():
        print(json.dumps({"note": f"{len(drafts)} draft frames found without a whole set; drawing them again"}), file=sys.stderr)
        for p_ in drafts:
            p_.unlink(missing_ok=True)
        record.unlink(missing_ok=True)
    cmd = [sys.executable, str(HERE / "capture_turntable.py"), "--workdir", str(wd), "--mode", "draft",
           "--frames", str(a.frames), "--size", str(a.size), "--timeout", str(a.timeout)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout + 60)
    line = next((l for l in reversed(p.stdout.splitlines()) if l.startswith("{")), None)
    out = json.loads(line) if line else {}
    if p.returncode != 0 or not out.get("ok") or not first.exists():
        reason = out.get("reason") or (p.stderr or p.stdout).strip()[-300:] or f"capture exit {p.returncode}"
        print(json.dumps({"ok": False, "reason": reason}))
        return 1
    print(json.dumps({"ok": True, "cached": False, "outputs": ["renders/draft_00.png", "renders/render.json"], "frames": out.get("frames"),
                      "res": out.get("res"), "wall_s": out.get("wall_s"), "studio": out.get("studio")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
