#!/usr/bin/env python3
"""Drive a studio render for one work dir. Converts 3mf to ply first (Blender has no 3mf importer).

  render_model.py --workdir DIR --mode draft [--frames 8]
  render_model.py --workdir DIR --mode final [--angles "35,125,215,305"] [--rotate x,y,z] [--color HEX] [--layer-lines]

Writes into DIR/renders/: draft_NN.png or hero.png + angle_NNN.png, and merges a
record into DIR/renders/render.json. Prints one JSON object. Exit 0 on success.
Needs BLENDER (path to the binary) in env, else /Applications/Blender.app/... or `blender` on PATH.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def blender() -> str | None:
    for cand in (os.environ.get("BLENDER"), "/Applications/Blender.app/Contents/MacOS/Blender", shutil.which("blender")):
        if cand and Path(cand).exists():
            return cand
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--mode", choices=["draft", "final"], default="draft")
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--angles", default="35,125,215,305")
    ap.add_argument("--elevation", type=float, default=18.0)
    ap.add_argument("--rotate", default="0,0,0")
    ap.add_argument("--color", default="#E8E4DC")
    ap.add_argument("--res", type=int)
    ap.add_argument("--samples", type=int)
    ap.add_argument("--layer-lines", action="store_true")
    a = ap.parse_args()
    wd = Path(a.workdir)
    out = wd / "renders"
    out.mkdir(parents=True, exist_ok=True)
    bl = blender()
    if not bl:
        print(json.dumps({"ok": False, "reason": "Blender not found: set BLENDER in store/.env"})); return 2
    sources = [p for p in wd.iterdir() if p.stem == "source"]
    if not sources:
        print(json.dumps({"ok": False, "reason": "no source.<ext> in workdir"})); return 1
    src = sources[0]
    display = wd / "display.ply"  # assembly_3mf.py: the parts placed as assembly.json says, in their colours
    if display.is_file():
        src = display
    elif src.suffix.lower() == ".3mf":
        conv = out / "render_input.ply"
        if not conv.exists():
            sys.path.insert(0, str(HERE))
            from load_3mf import load_3mf  # noqa: E402
            load_3mf(str(src)).export(str(conv))
        src = conv
    elif src.suffix.lower() not in (".stl", ".obj", ".ply"):
        print(json.dumps({"ok": False, "reason": f"cannot render {src.suffix}"})); return 1

    cmd = [bl, "--background", "--python", str(HERE / "render_scene.py"), "--",
           "--input", str(src), "--out", str(out), "--mode", a.mode, "--frames", str(a.frames),
           "--angles", a.angles, "--elevation", str(a.elevation), "--rotate", a.rotate, "--color", a.color]
    if a.res:
        cmd += ["--res", str(a.res)]
    if a.samples:
        cmd += ["--samples", str(a.samples)]
    if a.layer_lines:
        cmd.append("--layer-lines")
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    line = next((l for l in p.stdout.splitlines() if l.startswith("RENDER_RESULT ")), None)
    if p.returncode != 0 or not line:
        err = (p.stderr or p.stdout).strip().splitlines()[-8:]
        print(json.dumps({"ok": False, "reason": f"Blender exit {p.returncode}", "tail": err})); return 1
    result = json.loads(line[len("RENDER_RESULT "):])
    result["wall_s"] = round(time.time() - t0, 1)
    rec_p = out / "render.json"
    rec = json.loads(rec_p.read_text()) if rec_p.exists() else {}
    rec[a.mode] = result
    rec_p.write_text(json.dumps(rec, indent=2))
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
