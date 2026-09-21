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
    required_assembly   true when the assembled placement differs from the print layout.
                        The flag a person or the design brief may flip: renders show the
                        product assembled when true, as laid out for printing when false.
    multicolour         true when the parts use more than one extruder
    items               per build item: object_id, instance, name, extruder, colour,
                        build and assemble transforms (3mf 4x3, row-major, 12 numbers)
    filaments           per extruder: colour and type, as the designer's project had them

display.ply is what the renderers show instead of source.<ext>: every part
placed by `assemble` or by `build` according to the flag, with its filament
colour on its vertices when the product is multicolour. The slicer, the quote
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


def cmd_read(a) -> int:
    src = Path(a.src)
    if not src.is_file() or not zipfile.is_zipfile(src):
        return fail(f"{src} is not a 3mf")
    with zipfile.ZipFile(src) as z:
        names = set(z.namelist())
        if MODEL_PATH not in names:
            return fail("no 3D/3dmodel.model in the archive")
        items = build_items(z.read(MODEL_PATH))
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
    if complete and len(items) > 1:
        a_rel, b_rel = relative(items, "assemble"), relative(items, "build")
        required = bool(np.abs(a_rel - b_rel).max() > TOLERANCE_MM)
    used = sorted({i["extruder"] for i in items if i["extruder"]})
    doc = {
        "ok": True, "file": src.name,
        "source": "bambu assemble block" if complete else None,
        "required_assembly": required,
        "multicolour": len(used) > 1 and all(i["colour"] for i in items),
        "items": items,
        "filaments": [{"extruder": n + 1, "colour": c[:7].upper(), "type": types[n] if n < len(types) else None}
                      for n, c in enumerate(colours) if (n + 1) in used],
    }
    if a.out:
        Path(a.out).write_text(json.dumps(doc, indent=1) + "\n")
    return emit({k: doc[k] for k in ("ok", "source", "required_assembly", "multicolour", "filaments")} |
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
    vs, fs, cs, offset = [], [], [], 0
    for p in parts:
        vs.append(p["vertices"])
        fs.append(p["faces"] + offset)
        offset += len(p["vertices"])
        if coloured:
            hexa = (items.get((p["object_id"], p["instance"])) or {}).get("colour") or "#FFFFFF"
            rgb = struct.unpack("BBB", bytes.fromhex(hexa.lstrip("#")))
            cs.append(np.tile(np.array(rgb, dtype=np.uint8), (len(p["vertices"]), 1)))
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
