#!/usr/bin/env python3
"""Strip slicer project settings out of a .3mf so dapi's slicer accepts it.

Creality Print, OrcaSlicer and PrusaSlicer save "project" 3mf files that carry
the full printer, filament and process configuration of the machine they were
made on. dapi slices with Bambu Studio, which rejects a project whose settings
it does not recognise with "Invalid parameter value(s) included in the 3mf
file" (Creality's SPARKX i7 and K2 profiles are the ones seen so far). The
geometry itself is fine. This removes everything but the geometry and the
thumbnails, so the slicer treats the file as a plain model and applies its own
profile, which is what it does for an .stl.

    strip_3mf.py IN.3mf OUT.3mf          # write the stripped copy
    strip_3mf.py IN.3mf --check          # report only, exit 0 clean / 2 would strip

Output JSON: {ok, in, out, application, printer_model, removed: [...], kept: n,
bytes_in, bytes_out}. Exit 0 when written, 2 when --check finds settings, 1 on error.

Deterministic: same input, same output bytes (fixed zip timestamps, stored order).
The output has a different sha256 from the input, so intake must key its work
dir on the stripped file if that is what gets quoted.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

# Slicer state that makes a 3mf a "project". Geometry lives under 3D/, the
# package plumbing in _rels/ and [Content_Types].xml, previews in Metadata/*.png.
SETTINGS = re.compile(
    r"^Metadata/("
    r"project_settings\.config|model_settings\.config|slice_info\.config|"
    r"cut_information\.xml|custom_gcode_per_layer\.xml|creality\.config|"
    r"plate_\d+\.json|plate_\d+\.gcode(\.md5)?|"
    r"Slic3r_PE(_model)?\.config|Slic3r_PE\.config|prusaslicer\.config|"
    r".*\.config|.*\.json|.*\.gcode)$",
    re.I,
)
FIXED_DATE = (1980, 1, 1, 0, 0, 0)


def die(msg: str, code: int = 1) -> None:
    print(json.dumps({"ok": False, "error": msg}), file=sys.stderr)
    sys.exit(code)


def sniff(z: zipfile.ZipFile) -> tuple[str | None, str | None]:
    """Application that wrote the file and the printer it was set up for."""
    app = printer = None
    try:
        head = z.read("3D/3dmodel.model")[:4000].decode("utf-8", "replace")
        m = re.search(r'name="Application">([^<]+)<', head)
        app = m.group(1).strip() if m else None
    except KeyError:
        pass
    try:
        cfg = json.loads(z.read("Metadata/project_settings.config"))
        printer = cfg.get("printer_model") or cfg.get("printer_settings_id")
    except (KeyError, ValueError):
        pass
    return app, printer


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--check", action="store_true", help="report what would be removed, write nothing")
    args = ap.parse_args()

    src = Path(args.src)
    if not src.is_file():
        die(f"{src} not found")
    if not zipfile.is_zipfile(src):
        die(f"{src} is not a zip container, so not a 3mf")
    if not args.check and not args.dst:
        die("give an output path, or --check")

    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()
        if not any(n.startswith("3D/") for n in names):
            die("no 3D/ part in the archive; not a 3mf")
        app, printer = sniff(zin)
        removed = sorted(n for n in names if SETTINGS.match(n))
        kept = [n for n in names if n not in set(removed)]

        report = {"ok": True, "in": str(src), "out": args.dst, "application": app,
                  "printer_model": printer, "removed": removed, "kept": len(kept),
                  "bytes_in": src.stat().st_size}

        if args.check:
            report["ok"] = not removed
            print(json.dumps(report))
            return 2 if removed else 0

        dst = Path(args.dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for n in kept:
                info = zipfile.ZipInfo(n, date_time=FIXED_DATE)
                info.compress_type = zipfile.ZIP_DEFLATED
                zout.writestr(info, zin.read(n))

    report["bytes_out"] = dst.stat().st_size
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
