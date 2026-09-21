#!/usr/bin/env python3
"""The pictures a 3MF already carries, copied out of the work dir's source file.

  source_thumbnails.py --workdir work/<hash>

Bambu Studio and its forks write a rendered plate view (Metadata/plate_N.png,
the object in colour as arranged on the bed) and a top view (Metadata/top_N.png)
into every project. They are the designer's own pictures of the thing, free
the instant the file lands, and often show the object more plainly than a
grey draft turntable does. `strip_3mf.py` keeps them, so a stripped project
still has them.

Writes work/<hash>/thumbnails/<name>.png for every plate_N.png and top_N.png
found (the small, no-light and pick variants are skipped), and prints one
JSON line: {"ok": true, "thumbnails": ["thumbnails/plate_1.png", ...]}.
An empty list is the normal answer for an STL, an OBJ, or a 3MF exported
without previews (the cat clicker has none). Idempotent: files already
present are not rewritten. Exit 0 always except on a missing work dir or an
unreadable zip (exit 1). Decides nothing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

KEEP = re.compile(r"^Metadata/(plate|top)_(\d+)\.png$")


def extract(wd: Path) -> list[str]:
    """Copy the previews out of work/<hash>/source.3mf; the list of files written or already there."""
    src = wd / "source.3mf"
    if not src.is_file():
        return []
    out_dir = wd / "thumbnails"
    written: list[str] = []
    with zipfile.ZipFile(src) as z:
        names = sorted(n for n in z.namelist() if KEEP.match(n))
        for n in names:
            dst = out_dir / Path(n).name
            if not dst.exists():
                out_dir.mkdir(exist_ok=True)
                dst.write_bytes(z.read(n))
            written.append(f"thumbnails/{dst.name}")
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    a = ap.parse_args()
    wd = Path(a.workdir)
    if not wd.is_dir():
        print(json.dumps({"ok": False, "reason": f"no such work dir: {wd}"}), file=sys.stderr)
        return 1
    try:
        files = extract(wd)
    except zipfile.BadZipFile as e:
        print(json.dumps({"ok": False, "reason": f"source.3mf is not a readable zip: {e}"}), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "thumbnails": files}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
