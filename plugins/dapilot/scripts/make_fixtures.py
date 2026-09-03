#!/usr/bin/env python3
"""Generate test meshes into fixtures/.

single.stl  one closed body
multi.stl   eight separate closed bodies in one file, the "parts to assemble"
            case that only connected-component analysis can see

Existing files are left alone unless --force is given: the numbers quoted in
CLAUDE.md (8 bodies, 5,168 triangles for multi.stl) belong to the originals.

NOTE: reconstructed on 2026-09-03 after the original was lost; the original
generator's exact primitives are unknown, so regenerated fixtures differ in
triangle count from the ones quoted in CLAUDE.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import trimesh

OUT = Path(__file__).resolve().parents[3] / "fixtures"


def single() -> trimesh.Trimesh:
    # 20 * 4^6 = 81,920 faces, the count the original single.stl has.
    return trimesh.creation.icosphere(subdivisions=6, radius=40.0)


def multi() -> trimesh.Trimesh:
    parts = []
    rng = np.random.default_rng(7)
    for i in range(8):
        kind = i % 4
        if kind == 0:
            p = trimesh.creation.box(extents=(12, 8, 6))
        elif kind == 1:
            p = trimesh.creation.cylinder(radius=4, height=10, sections=24)
        elif kind == 2:
            p = trimesh.creation.icosphere(subdivisions=2, radius=5)
        else:
            p = trimesh.creation.capsule(radius=3, height=8, count=(12, 12))
        p.apply_translation((i * 20.0, rng.uniform(-3, 3), 0.0))
        parts.append(p)
    return trimesh.util.concatenate(parts)


def main() -> int:
    force = "--force" in sys.argv
    OUT.mkdir(exist_ok=True)
    for name, fn in (("single.stl", single), ("multi.stl", multi)):
        dest = OUT / name
        if dest.exists() and not force:
            print(f"keep  {dest} (use --force to regenerate)")
            continue
        m = fn()
        m.export(dest)
        print(f"wrote {dest}: {len(m.faces)} triangles, {len(m.split(only_watertight=False))} bodies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
