#!/usr/bin/env python3
"""Stream a .3mf into numpy arrays.

Why this exists: trimesh's 3mf path builds an XML DOM and per-vertex Python
objects before anything reaches numpy. dapi measured an 8.4MB project taking
183 seconds and peaking at 6.08 GB RSS - 725x the file size - which is why
tools/bulk-slice/mesh_bbox.py skips .3mf entirely. Blender has no 3mf importer
either (4.5 ships stl/obj/ply/fbx/usd/collada/alembic and nothing else).

A 3mf is a zip whose 3D/3dmodel.model is plain XML: a vertex list and a
triangle list per object, plus a build section placing those objects with a
transform. iterparse walks it as a stream and clears each element after use, so
peak memory is the numpy arrays rather than the whole document.

Bambu wraps geometry in a component: the mesh sits on one object, a second
component-only object references it (sometimes from a separate model file
under 3D/Objects/ via a `path` attribute), and the build item points at the
second. Components are resolved recursively with transforms composed.

NOTE: this file was reconstructed on 2026-09-03 after the original was lost.
It reproduces the original's measured output on the shark fixture (122,444
triangles, 15 bodies, all solid). Treat any divergence from that as a bug here.
"""
from __future__ import annotations

import json
import sys
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
import trimesh

MODEL_PATH = "3D/3dmodel.model"
# A printable part expands to 8-25MB. Well past that is a decompression bomb or
# a file that has no business in a print queue either way.
MAX_UNCOMPRESSED = 600 * 1024 * 1024

# 3mf units -> millimetres
UNIT_MM = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0, "inch": 25.4, "foot": 304.8, "meter": 1000.0}


def _local(tag: str) -> str:
    """Strip the XML namespace: 3mf declares one and nobody agrees which."""
    return tag.rsplit("}", 1)[-1]


def _attr(el: ET.Element, name: str) -> str | None:
    """Attribute lookup ignoring namespace prefixes (Bambu's p:path etc)."""
    if name in el.attrib:
        return el.attrib[name]
    for k, v in el.attrib.items():
        if _local(k) == name:
            return v
    return None


def _parse_transform(text: str | None) -> np.ndarray:
    """3mf stores a 4x3 row-major transform as 12 space-separated floats.

    Rows are m00 m01 m02 / m10 m11 m12 / m20 m21 m22 / m30 m31 m32 and it is
    applied to row vectors: [x y z 1] * M. Convert to a 4x4 for column vectors.
    """
    if not text:
        return np.eye(4)
    v = [float(x) for x in text.split()]
    if len(v) != 12:
        raise ValueError(f"bad 3mf transform: {text!r}")
    m = np.eye(4)
    m[:3, :3] = np.array(v[:9]).reshape(3, 3).T
    m[:3, 3] = v[9:12]
    return m


class _Model:
    """Objects from one model file: id -> ('mesh', verts, faces) | ('comp', [(ref, transform)])."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple] = {}
        self.build: list[tuple[str, str | None, np.ndarray]] = []  # (objectid, path, transform)
        self.unit = "millimeter"


def _parse_model(data: bytes) -> _Model:
    model = _Model()
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    comps: list[tuple[str, str | None, np.ndarray]] = []
    obj_id: str | None = None
    it = ET.iterparse(_Bytes(data), events=("start", "end"))
    for event, el in it:
        tag = _local(el.tag)
        if event == "start":
            if tag == "model":
                model.unit = el.attrib.get("unit", "millimeter")
            elif tag == "object":
                obj_id = el.attrib.get("id")
                verts, faces, comps = [], [], []
            continue
        # end events
        if tag == "vertex":
            verts.append((float(el.attrib["x"]), float(el.attrib["y"]), float(el.attrib["z"])))
        elif tag == "triangle":
            faces.append((int(el.attrib["v1"]), int(el.attrib["v2"]), int(el.attrib["v3"])))
        elif tag == "component":
            comps.append((el.attrib["objectid"], _attr(el, "path"), _parse_transform(el.attrib.get("transform"))))
        elif tag == "item":
            model.build.append((el.attrib["objectid"], _attr(el, "path"), _parse_transform(el.attrib.get("transform"))))
        elif tag == "object" and obj_id is not None:
            if faces:
                model.objects[obj_id] = ("mesh", np.asarray(verts, dtype=np.float64), np.asarray(faces, dtype=np.int64))
            elif comps:
                model.objects[obj_id] = ("comp", list(comps))
            obj_id = None
            verts, faces, comps = [], [], []
        el.clear()
    return model


class _Bytes:
    """Minimal file-like over bytes for iterparse without copying into BytesIO twice."""

    def __init__(self, data: bytes) -> None:
        self._data = memoryview(data)
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            n = len(self._data) - self._pos
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk.tobytes()


def load_3mf(path: str) -> trimesh.Trimesh:
    """Return one processed Trimesh holding every built object, in millimetres."""
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        if MODEL_PATH not in names:
            raise ValueError("not a 3mf: missing 3D/3dmodel.model")
        total = sum(i.file_size for i in zf.infolist() if i.filename.endswith(".model"))
        if total > MAX_UNCOMPRESSED:
            raise ValueError(f"model XML expands to {total} bytes, over the {MAX_UNCOMPRESSED} limit")

        models: dict[str, _Model] = {}

        def get_model(rel: str | None) -> _Model:
            key = MODEL_PATH if not rel else rel.lstrip("/")
            if key not in models:
                if key not in names:
                    raise ValueError(f"3mf references missing model file {key}")
                models[key] = _parse_model(zf.read(key))
            return models[key]

        root = get_model(None)
        scale = UNIT_MM.get(root.unit, 1.0)

        chunks_v: list[np.ndarray] = []
        chunks_f: list[np.ndarray] = []
        offset = 0

        def resolve(model_key: str | None, obj_id: str, xf: np.ndarray, depth: int = 0) -> None:
            nonlocal offset
            if depth > 32:
                raise ValueError("component nesting too deep; refusing a possible cycle")
            model = get_model(model_key)
            if obj_id not in model.objects:
                return  # an object with neither mesh nor components (metadata only)
            kind, *payload = model.objects[obj_id]
            if kind == "mesh":
                v, f = payload
                if len(f) == 0:
                    return
                vh = np.c_[v, np.ones(len(v))] @ xf.T
                chunks_v.append(vh[:, :3])
                chunks_f.append(f + offset)
                offset += len(v)
            else:
                for ref_id, ref_path, ref_xf in payload[0]:
                    resolve(ref_path or model_key, ref_id, xf @ ref_xf, depth + 1)

        if root.build:
            for obj_id, obj_path, xf in root.build:
                resolve(obj_path, obj_id, xf)
        else:
            for obj_id in root.objects:
                resolve(None, obj_id, np.eye(4))

    if not chunks_f:
        raise ValueError("3mf contains no triangles")
    vertices = np.vstack(chunks_v) * scale
    faces = np.vstack(chunks_f)
    # process=True merges duplicate vertices; split() needs that to find bodies.
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=True)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: load_3mf.py model.3mf", file=sys.stderr)
        return 2
    m = load_3mf(sys.argv[1])
    print(json.dumps({"triangles": int(len(m.faces)), "vertices": int(len(m.vertices)),
                      "bbox_mm": [round(float(x), 2) for x in m.extents]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
