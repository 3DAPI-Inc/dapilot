#!/usr/bin/env python3
"""Deterministic geometry facts for one model file.

Output is a few hundred bytes of numbers. That is the point: an STL is a
triangle soup with no units, names or hierarchy, and no amount of context
window makes 80k float triples readable. Everything downstream that needs to
reason about the model reasons about this summary instead.

Usage: analyze_mesh.py model.stl [--out analysis.json]

With --out the result is written to that path only when the analysis
succeeded, and atomically: to a temporary file beside it, then renamed into
place. A failure writes nothing, so a reader that finds the file can trust
it. Before this (2026-10-25) the skill redirected stdout into analysis.json,
and a worker that died mid-step left a 0-byte file the ledger took for a
finished stage; research then failed on the same item in every later run.
The JSON is printed to stdout either way.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import trimesh

from load_3mf import load_3mf

# Guard against a pathological upload eating the box. Real printable parts in
# the 3DAPI catalogue land at 8-25MB.
MAX_BYTES = 600 * 1024 * 1024


def analyze(path: str) -> dict:
    # .3mf goes through our own streaming loader. trimesh's 3mf path builds an
    # XML DOM and peaks in the gigabytes; ours took 1.1s and 0.14GB on the same
    # file that 3DAPI measured at 183s and 6.08GB. See load_3mf.py.
    if path.lower().endswith(".3mf"):
        mesh = load_3mf(path)
    else:
        scene_or_mesh = trimesh.load(path)  # NOT process=False: see bodies below
        if isinstance(scene_or_mesh, trimesh.Scene):
            if not scene_or_mesh.geometry:
                return {"ok": False, "reason": "no geometry in file"}
            mesh = scene_or_mesh.to_mesh()
        else:
            mesh = scene_or_mesh

    # An STL holds one unstructured triangle soup no matter how many separate
    # objects the designer put in it, so "15 parts to assemble" is invisible
    # structurally. split() walks face adjacency to recover them, which only
    # works because vertices were merged first. Unmerged, no two triangles share
    # a vertex and this returns one body per face.
    bodies = mesh.split(only_watertight=False)
    watertight = bool(mesh.is_watertight)
    solid_bodies = [b for b in bodies if b.is_watertight]
    all_solid = len(solid_bodies) == len(bodies)

    # Volume is only meaningful on a closed surface, and trimesh happily returns
    # a confident wrong number for an open one. But judge it PER BODY, not on the
    # union: an articulated print has interlocking parts, so the combined mesh
    # reports non-watertight while every individual piece is closed. The shark
    # fixture is exactly this - 15 of 15 bodies solid, union not. Summing the
    # bodies is the number that is actually true.
    if all_solid:
        volume = round(sum(abs(b.volume) for b in bodies), 1)
    elif watertight:
        volume = round(float(abs(mesh.volume)), 1)
    else:
        volume = None

    sizes = sorted((abs(b.volume) if b.is_watertight else b.area for b in bodies), reverse=True)
    total = sum(sizes) or 1.0

    return {
        "ok": True,
        "triangles": int(len(mesh.faces)),
        "bbox_mm": [round(float(d), 2) for d in mesh.extents],
        "volume_mm3": volume,
        "area_mm2": round(float(mesh.area), 1),
        "watertight": watertight,
        "winding_consistent": bool(mesh.is_winding_consistent),
        "bodies": len(bodies),
        "watertight_bodies": sum(1 for b in bodies if b.is_watertight),
        "largest_body_frac": round(sizes[0] / total, 3) if sizes else None,
    }


def write_atomic(path: str, text: str) -> None:
    """The whole file or no file: written beside the target, fsynced, then renamed over it."""
    target = os.path.abspath(path)
    tmp = f"{target}.tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic geometry facts for one model file.")
    ap.add_argument("model", help="model.stl, .3mf or .obj")
    ap.add_argument("--out", help="write the result here, atomically, and only on success")
    a = ap.parse_args()
    try:
        result = analyze(a.model)
    except Exception as exc:  # noqa: BLE001 - any loader failure is just a gap
        result = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    text = json.dumps(result, indent=2)
    if a.out and result.get("ok"):
        try:
            write_atomic(a.out, text)
        except OSError as exc:
            result = {"ok": False, "reason": f"could not write {a.out}: {exc}"}
            text = json.dumps(result, indent=2)
    print(text)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
