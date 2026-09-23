#!/usr/bin/env python3
"""How a multi-part 3mf goes together, and in which colours: read it, keep it, rebuild it.

A Bambu Studio project places its objects twice. `<build>` in 3D/3dmodel.model
is the print layout: parts spread over plates. `<assemble>` in
Metadata/model_settings.config is the Assembly View: where each part sits in
the finished product, kept whenever the designer split one import into objects.
The same file names each object's extruder, and project_settings.config the
filament colour per extruder. strip_3mf.py removes both files so dapi's slicer
accepts the project, so this script reads them first, from the original upload.

    assembly_3mf.py read  ORIGINAL.3mf [--out assembly.json]
    assembly_3mf.py build --workdir work/<hash> [--pose assembled|as_printed]   -> display.ply

assembly.json:
    required_assembly   true when the assembled placement differs from the print layout
                        AND the parts meet there. Bambu's Assembly View lines objects that
                        were never put together up in a row, so a placement that differs is
                        only an assembly when every part sits within CONTACT_MM of another
                        (axis-aligned boxes, one connected set); a welder jig read as a
                        479 mm row of three parts on 2026-09-23. The flag a person or the
                        design brief may flip: renders show the product assembled when true,
                        as laid out for printing when false.
    contact             the test behind the flag when the placements differ: connected,
                        parts, min_gap_mm, apart (parts not joined to the first)
    multicolour         true when the parts use more than one extruder, by object or by paint
    painted             extruders Bambu's paint tool puts on triangles (`paint_color`), [] when none
    items               per build item: object_id, instance, name, extruder, colour,
                        build and assemble transforms (3mf 4x3, row-major, 12 numbers)
    filaments           per extruder: colour and type, as the designer's project had them

display.ply is what the renderers show instead of source.<ext>: every part
placed by `assemble` or by `build` according to the flag, with its filament
colour on its vertices when the product is multicolour. A painted model gets its
colour per triangle (vertices unshared, so each face keeps its own colour); a
triangle split by the brush shows its most common state. The slicer, the quote
and analyze_mesh.py keep using source.<ext>; nothing here changes what is printed.

Deterministic, no judgement. Exit 0 ok, 1 failed. `read` on a file with no
assemble block is ok with required_assembly false.
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_3mf import MODEL_PATH, _parse_transform, load_3mf_parts  # noqa: E402

SETTINGS = "Metadata/model_settings.config"
PROJECT = "Metadata/project_settings.config"
IDENTITY = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
TOLERANCE_MM = 0.05
CONTACT_MM = 1.0     # parts this close in the assembled pose count as meeting


def emit(doc: dict) -> int:
    print(json.dumps(doc))
    return 0 if doc.get("ok") else 1


def fail(reason: str) -> int:
    return emit({"ok": False, "reason": reason})


def numbers(text: str | None) -> list[float] | None:
    try:
        v = [float(x) for x in (text or "").split()]
    except ValueError:
        return None
    return v if len(v) == 12 else None


def paint_state(code: str | None) -> int:
    """The extruder a painted triangle shows: 0 for the object's own, else 1-based.

    Bambu and PrusaSlicer store the brush as a tree, hex nibbles read from the end.
    A nibble's low two bits are how many sides were split (0: a leaf); a leaf's high
    two bits are its state, and 3 there means the next nibble holds state - 3. A split
    triangle has sides + 1 children. The renders only need one colour per triangle,
    so this counts the leaves and returns the most common state.
    """
    if not code:
        return 0
    nibbles = [int(c, 16) for c in reversed(code.strip()) if c in "0123456789abcdefABCDEF"]
    counts: dict[int, int] = {}
    pos = 0

    def walk(depth: int = 0) -> None:
        nonlocal pos
        if pos >= len(nibbles) or depth > 32:
            return
        n = nibbles[pos]
        pos += 1
        split = n & 3
        if split:
            for _ in range(split + 1):
                walk(depth + 1)
            return
        state = n >> 2
        if state == 3 and pos < len(nibbles):
            state = 3 + nibbles[pos]
            pos += 1
        counts[state] = counts.get(state, 0) + 1

    walk()
    return max(counts, key=lambda k: (counts[k], -k)) if counts else 0


def painted_states(z: zipfile.ZipFile, names: set[str]) -> set[int]:
    """Extruders the paint tool uses anywhere in the model, without parsing the meshes."""
    codes: set[bytes] = set()
    for n in names:
        if n.endswith(".model"):
            codes.update(re.findall(rb'(?:paint_color|mmu_segmentation)="([0-9A-Fa-f]+)"', z.read(n)))
    return {paint_state(c.decode()) for c in codes} - {0}


def build_items(root_model: bytes) -> list[dict]:
    """The <build> block, which closes the root model file, without parsing the meshes before it."""
    tail = root_model[root_model.rfind(b"<build"):].decode("utf-8", "replace")
    seen: dict[str, int] = {}
    items = []
    for m in re.finditer(r"<item\b([^>]*)>", tail):
        attrs = dict(re.findall(r'([\w:]+)="([^"]*)"', m.group(1)))
        oid = attrs.get("objectid")
        if oid is None:
            continue
        instance = seen.get(oid, 0)
        seen[oid] = instance + 1
        items.append({"object_id": oid, "instance": instance, "build": numbers(attrs.get("transform")) or IDENTITY})
    return items


def relative(items: list[dict], key: str) -> np.ndarray:
    """Every placement expressed in the first item's frame, so a layout moved as a whole compares equal."""
    mats = [_parse_transform(" ".join(str(x) for x in i[key])) for i in items]
    inv = np.linalg.inv(mats[0])
    return np.array([inv @ m for m in mats])


def parts_touch(path: Path, items: list[dict], key: str = "assemble") -> dict:
    """Whether the parts, placed by `key`, form one piece: each within CONTACT_MM of another, by axis-aligned
    boxes, and all joined through such contacts. Boxes are coarse (an interlocking part overlaps its
    neighbour's box anyway); what they reject is a row of parts with daylight between them."""
    placements = {(i["object_id"], i["instance"]): _parse_transform(" ".join(str(x) for x in i[key])) for i in items}
    parts = load_3mf_parts(str(path), placements)
    boxes = [(p["vertices"].min(0), p["vertices"].max(0)) for p in parts]
    names = {(i["object_id"], i["instance"]): i.get("name") for i in items}
    n = len(boxes)
    sep = np.zeros((n, n))
    for a in range(n):
        for b in range(a + 1, n):
            lo_a, hi_a = boxes[a]
            lo_b, hi_b = boxes[b]
            # the widest separation along any axis; at or below zero the boxes intersect
            sep[a, b] = sep[b, a] = float(np.max(np.maximum(lo_a - hi_b, lo_b - hi_a)))
    joined = {0}
    frontier = [0]
    while frontier:
        a = frontier.pop()
        for b in range(n):
            if b not in joined and sep[a, b] <= CONTACT_MM:
                joined.add(b)
                frontier.append(b)
    apart = [names.get((parts[b]["object_id"], parts[b]["instance"])) or parts[b]["object_id"] for b in range(n) if b not in joined]
    gaps = sep[np.triu_indices(n, 1)]
    return {"connected": len(joined) == n, "parts": n, "min_gap_mm": round(float(max(gaps.min(), 0.0)), 2) if n > 1 else 0.0,
            "apart": apart}


def cmd_read(a) -> int:
    src = Path(a.src)
    if not src.is_file() or not zipfile.is_zipfile(src):
        return fail(f"{src} is not a 3mf")
    with zipfile.ZipFile(src) as z:
        names = set(z.namelist())
        if MODEL_PATH not in names:
            return fail("no 3D/3dmodel.model in the archive")
        items = build_items(z.read(MODEL_PATH))
        painted = painted_states(z, names)
        objects: dict[str, dict] = {}
        assemble: dict[tuple[str, int], list[float]] = {}
        if SETTINGS in names:
            try:
                root = ET.fromstring(z.read(SETTINGS))
            except ET.ParseError as e:
                return fail(f"{SETTINGS} is not XML: {e}")
            for obj in root.iter("object"):
                meta = {m.get("key"): m.get("value") for m in obj.findall("metadata")}
                objects[obj.get("id")] = {"name": meta.get("name"), "extruder": int(meta["extruder"]) if (meta.get("extruder") or "").isdigit() else None}
            for it in root.iter("assemble_item"):
                t = numbers(it.get("transform"))
                if t:
                    assemble[(it.get("object_id"), int(it.get("instance_id") or 0))] = t
        colours, types = [], []
        if PROJECT in names:
            try:
                project = json.loads(z.read(PROJECT))
                colours, types = project.get("filament_colour") or [], project.get("filament_type") or []
            except ValueError:
                pass

    for i in items:
        info = objects.get(i["object_id"], {})
        i["name"], i["extruder"] = info.get("name"), info.get("extruder")
        i["assemble"] = assemble.get((i["object_id"], i["instance"]))
        e = i["extruder"]
        i["colour"] = colours[e - 1][:7].upper() if e and 0 < e <= len(colours) else None

    complete = bool(items) and all(i["assemble"] for i in items)
    required = False
    contact = None
    if complete and len(items) > 1:
        a_rel, b_rel = relative(items, "assemble"), relative(items, "build")
        if bool(np.abs(a_rel - b_rel).max() > TOLERANCE_MM):
            # A different placement is an assembly only when the parts meet there.
            try:
                contact = parts_touch(src, items, "assemble")
                required = contact["connected"]
            except ValueError as e:   # a model too large to load here: keep the old reading, and say so
                contact = {"error": str(e)}
                required = True
    used = sorted({i["extruder"] for i in items if i["extruder"]} | painted)
    known = all(0 < e <= len(colours) for e in used)
    doc = {
        "ok": True, "file": src.name,
        "source": "bambu assemble block" if complete else None,
        "required_assembly": required,
        "contact": contact,
        "multicolour": len(used) > 1 and known and all(i["colour"] for i in items),
        "painted": sorted(painted),
        "items": items,
        "filaments": [{"extruder": n + 1, "colour": c[:7].upper(), "type": types[n] if n < len(types) else None}
                      for n, c in enumerate(colours) if (n + 1) in used],
    }
    if a.out:
        Path(a.out).write_text(json.dumps(doc, indent=1) + "\n")
    return emit({k: doc[k] for k in ("ok", "source", "required_assembly", "contact", "multicolour", "painted", "filaments")} |
                {"items": len(items), "out": a.out})


def write_ply(path: Path, vertices: np.ndarray, faces: np.ndarray, colours: np.ndarray | None) -> None:
    header = ["ply", "format binary_little_endian 1.0", f"element vertex {len(vertices)}",
              "property float x", "property float y", "property float z"]
    if colours is not None:
        header += ["property uchar red", "property uchar green", "property uchar blue"]
    header += [f"element face {len(faces)}", "property list uchar int vertex_indices", "end_header"]
    vtype = [("xyz", "<f4", 3)] + ([("rgb", "u1", 3)] if colours is not None else [])
    v = np.zeros(len(vertices), dtype=vtype)
    v["xyz"] = vertices
    if colours is not None:
        v["rgb"] = colours
    f = np.zeros(len(faces), dtype=[("n", "u1"), ("idx", "<i4", 3)])
    f["n"], f["idx"] = 3, faces
    with path.open("wb") as out:
        out.write(("\n".join(header) + "\n").encode())
        out.write(v.tobytes())
        out.write(f.tobytes())


def cmd_build(a) -> int:
    wd = Path(a.workdir)
    try:
        doc = json.loads((wd / "assembly.json").read_text())
    except (OSError, ValueError):
        return fail("assembly.json is missing or not JSON; `read` writes it from the original upload")
    source = wd / "source.3mf"
    if not source.is_file():
        return fail("no source.3mf in the work dir")
    assembled = bool(doc.get("required_assembly")) if a.pose is None else a.pose == "assembled"
    items = {(i["object_id"], i["instance"]): i for i in doc.get("items") or []}
    placements = None
    if assembled:
        missing = [k for k, i in items.items() if not i.get("assemble")]
        if missing:
            return fail(f"required_assembly is true but these items have no assemble transform: {missing}")
        placements = {k: _parse_transform(" ".join(str(x) for x in i["assemble"])) for k, i in items.items()}
    try:
        parts = load_3mf_parts(str(source), placements)
    except ValueError as e:
        return fail(str(e))

    coloured = bool(doc.get("multicolour"))
    palette = {f["extruder"]: f["colour"] for f in doc.get("filaments") or [] if f.get("colour")}

    def rgb(hexa: str | None) -> np.ndarray:
        return np.array(struct.unpack("BBB", bytes.fromhex((hexa or "#FFFFFF").lstrip("#"))), dtype=np.uint8)

    vs, fs, cs, offset = [], [], [], 0
    for p in parts:
        own = (items.get((p["object_id"], p["instance"])) or {}).get("colour")
        if coloured and p.get("paint"):
            # One colour per triangle: every face gets its own three vertices, so a painted face
            # does not bleed its colour into the unpainted face beside it.
            v = p["vertices"][p["faces"]].reshape(-1, 3)
            cache: dict[str | None, np.ndarray] = {}
            face_rgb = np.empty((len(p["faces"]), 3), dtype=np.uint8)
            for i, code in enumerate(p["paint"]):
                if code not in cache:
                    state = paint_state(code)
                    cache[code] = rgb(palette.get(state) if state else own)
                face_rgb[i] = cache[code]
            vs.append(v)
            fs.append(np.arange(len(v)).reshape(-1, 3) + offset)
            cs.append(np.repeat(face_rgb, 3, axis=0))
            offset += len(v)
            continue
        vs.append(p["vertices"])
        fs.append(p["faces"] + offset)
        offset += len(p["vertices"])
        if coloured:
            cs.append(np.tile(rgb(own), (len(p["vertices"]), 1)))
    vertices, faces = np.vstack(vs), np.vstack(fs)
    write_ply(wd / "display.ply", vertices, faces, np.vstack(cs) if coloured else None)
    extents = (vertices.max(0) - vertices.min(0)).round(1).tolist()
    # The size of the product as shown. analysis.json's bbox_mm is the print layout, which for parts spread
    # over plates is several times the product; whoever needs the product's size reads this when it exists.
    (wd / "display.json").write_text(json.dumps({"pose": "assembled" if assembled else "as_printed",
                                                 "multicolour": coloured, "bbox_mm": extents}, indent=1) + "\n")
    return emit({"ok": True, "display": "display.ply", "pose": "assembled" if assembled else "as_printed",
                 "multicolour": coloured, "parts": len(parts), "triangles": int(len(faces)), "bbox_mm": extents})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read")
    r.add_argument("src")
    r.add_argument("--out")
    b = sub.add_parser("build")
    b.add_argument("--workdir", required=True)
    b.add_argument("--pose", choices=["assembled", "as_printed"], default=None,
                   help="override assembly.json's required_assembly for this build; the file is not changed")
    a = ap.parse_args()
    return {"read": cmd_read, "build": cmd_build}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
