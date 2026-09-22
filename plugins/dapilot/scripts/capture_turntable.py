#!/usr/bin/env python3
"""Capture a studio turntable from the browser scene and encode it.

  capture_turntable.py --workdir DIR [--frames 120] [--size 720] [--fps 24]
                       [--passes beauty,depth,normal] [--start-yaw 35] [--no-video]
                       [--out DIR/renders] [--rotate x,y,z] [--elevation 18]
  capture_turntable.py --workdir DIR --yaws 20,110,200,290 --size 2048 --passes beauty --no-video

Writes OUT/<pass>/frame_0000.png ... , OUT/turntable.mp4 and a record at
OUT/video.json. OUT defaults to DIR/renders. Prints one JSON object. Exit 0 on
success.

Yaw follows render_scene.py: yaw N shows the side the Blender draft at yaw N
shows. --rotate is the same orientation render_model.py takes. --yaws captures
exactly those angles, one frame each, which is what product stills want.

Why a browser: WebGL reaches a GPU that Cycles cannot on this hardware, so a
rotation costs seconds instead of an hour. The depth and normal passes come off
the same scene for one extra draw each, and they are what a diffusion model
conditions on.

How it works: this script serves a collector on a loopback port, opens the
studio page headless with capture=1 pointing at it, and writes every frame the
page posts. The page owns the stepping because only it knows when a frame is
truly on the canvas; this script owns the disk and the encode.

Needs: a running studio page (npm run dev in frontend/), Chrome, and ffmpeg.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PASSES = ("beauty", "depth", "normal")


def chrome() -> str | None:
    for cand in (
        os.environ.get("CHROME"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ):
        if cand and Path(cand).exists():
            return cand
    return None


class Collector(ThreadingHTTPServer):
    """Holds what the page has sent so far. One instance per run."""

    daemon_threads = True

    def __init__(self, out: Path, source: Path):
        super().__init__(("127.0.0.1", 0), Handler)
        self.out = out
        # Served by us, not by the dev server: Vite refuses paths outside the
        # repo, and a work dir can live anywhere.
        self.source = source
        self.model_served = False
        self.counts: dict[str, int] = {p: 0 for p in PASSES}
        self.errors: list[str] = []
        self.last = time.time()

    @property
    def port(self) -> int:
        return self.socket.getsockname()[1]

    @property
    def total(self) -> int:
        return sum(self.counts.values())


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802  (BaseHTTPRequestHandler's spelling)
        server: Collector = self.server  # type: ignore[assignment]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        name = (query.get("pass") or [""])[0]
        index = (query.get("index") or [""])[0]
        if name not in PASSES or not index.isdigit():
            self.send_error(400, "pass and index are required")
            return
        try:
            body = self.rfile.read(int(self.headers.get("content-length", 0)))
            target = server.out / name
            target.mkdir(parents=True, exist_ok=True)
            (target / f"frame_{int(index):04d}.png").write_bytes(body)
            server.counts[name] += 1
            server.last = time.time()
        except Exception as exc:                      # a bad frame must stop the run
            server.errors.append(f"{name} {index}: {exc}")
            self.send_error(500, "could not write the frame")
            return
        self.send_response(204)
        self.send_header("access-control-allow-origin", "*")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        server: Collector = self.server  # type: ignore[assignment]
        if not urllib.parse.urlparse(self.path).path.startswith("/model"):
            self.send_error(404, "only /model is served here")
            return
        body = server.source.read_bytes()
        server.model_served = True
        self.send_response(200)
        self.send_header("content-type", "application/octet-stream")
        self.send_header("content-length", str(len(body)))
        self.send_header("access-control-allow-origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-methods", "POST, OPTIONS")
        self.send_header("access-control-allow-headers", "*")
        self.end_headers()

    def log_message(self, *_args) -> None:            # the driver prints, not the server
        return


def page_is_up(base: str) -> bool:
    try:
        with urllib.request.urlopen(base, timeout=3) as res:
            return res.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def encode(frames_dir: Path, out: Path, fps: int) -> tuple[bool, str]:
    """H.264, yuv420p, even dimensions. Silent: Etsy strips audio anyway."""
    cmd = [
        "ffmpeg", "-y", "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%04d.png"),
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-an", str(out),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode == 0, (p.stderr or "").strip().splitlines()[-1] if p.returncode else ""


def fail(reason: str, **extra) -> int:
    print(json.dumps({"ok": False, "reason": reason, **extra}))
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--frames", type=int, default=120)
    ap.add_argument("--size", type=int, default=720)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--passes", default="beauty,depth,normal")
    ap.add_argument("--start-yaw", type=float, default=0.0)
    ap.add_argument("--arc", type=float, default=360.0,
                    help="total degrees swept; 360 is a turntable, 20 is a slow drift")
    ap.add_argument("--yaws", default=None, help="comma-separated exact angles; replaces --frames, --start-yaw, --arc")
    ap.add_argument("--rotate", default=None, help="orientation x,y,z in degrees, as render_model.py")
    ap.add_argument("--elevation", type=float, default=None, help="camera height in degrees above the horizon")
    ap.add_argument("--out", default=None, help="output directory; default WORKDIR/renders")
    ap.add_argument("--video-name", default="turntable.mp4")
    ap.add_argument("--color", default=None, help="filament colour hex, e.g. '#3B7DD8'")
    ap.add_argument("--studio", default=os.environ.get("DAPILOT_STUDIO_URL", "http://localhost:8081"))
    ap.add_argument("--no-video", action="store_true", help="frames only, skip ffmpeg")
    ap.add_argument("--timeout", type=float, default=900.0)
    a = ap.parse_args()

    wd = Path(a.workdir).resolve()
    sources = [p for p in wd.iterdir() if p.stem == "source"] if wd.is_dir() else []
    if not sources:
        return fail("no source.<ext> in workdir")
    if (wd / "display.ply").is_file():  # assembly_3mf.py: the parts placed as assembly.json says, in their colours
        sources = [wd / "display.ply"]
    passes = [p.strip() for p in a.passes.split(",") if p.strip() in PASSES]
    if not passes:
        return fail(f"--passes must be some of {', '.join(PASSES)}")
    yaws: list[float] = []
    if a.yaws:
        try:
            yaws = [float(v) for v in a.yaws.split(",") if v.strip()]
        except ValueError:
            return fail("--yaws must be numbers separated by commas")
        a.frames = len(yaws)
    if a.rotate:
        try:
            if len([float(v) for v in a.rotate.split(",")]) != 3:
                raise ValueError
        except ValueError:
            return fail("--rotate must be three numbers, x,y,z")

    browser = chrome()
    if not browser:
        return fail("Chrome not found: set CHROME to the binary")
    if not a.no_video and not shutil.which("ffmpeg"):
        return fail("ffmpeg not found; install it or pass --no-video")
    base = a.studio.rstrip("/")
    if not page_is_up(base):
        return fail(f"no studio page at {base}: start it with `npm run dev` in frontend/")

    out = Path(a.out).resolve() if a.out else wd / "renders"
    out.mkdir(parents=True, exist_ok=True)
    for name in passes:
        for stale in (out / name).glob("frame_*.png"):
            stale.unlink()                            # a rerun must not blend two sequences

    server = Collector(out, sources[0])
    threading.Thread(target=server.serve_forever, daemon=True).start()

    model_url = f"http://127.0.0.1:{server.port}/model{sources[0].suffix}"
    page = f"{base}/studio?" + urllib.parse.urlencode({
        "model": model_url, "capture": "1", "frames": a.frames, "size": a.size,
        "passes": ",".join(passes), "startYaw": a.start_yaw, "arc": a.arc,
        **({"yaws": ",".join(f"{y:g}" for y in yaws)} if yaws else {}),
        **({"rotate": a.rotate} if a.rotate else {}),
        **({"elevation": a.elevation} if a.elevation is not None else {}),
        **({"color": a.color} if a.color else {}),
        "post": f"http://127.0.0.1:{server.port}/frame",
    })

    profile = out / ".chrome-capture"
    linux = []
    if sys.platform.startswith("linux"):
        # A server has no GPU: WebGL falls back to SwiftShader, which Chrome only allows when asked.
        linux = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
        if os.geteuid() == 0:
            linux.append("--no-sandbox")   # Chrome refuses to start as root otherwise
    proc = subprocess.Popen(
        [browser, "--headless=new", "--disable-gpu-sandbox", "--hide-scrollbars", *linux,
         "--window-size=1400,1400", f"--user-data-dir={profile}", page],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    expected = a.frames * len(passes)
    t0 = time.time()
    try:
        while server.total < expected:
            if proc.poll() is not None:
                return fail("the browser exited before the run finished",
                            captured=server.counts, errors=server.errors)
            if server.errors:
                return fail(f"a frame could not be written: {server.errors[0]}", captured=server.counts)
            waited = time.time() - t0
            if not server.model_served and waited > 45:
                return fail("the page never fetched the model; is /studio built and reachable?")
            # Two clocks: a hard cap, and a stall guard once frames start arriving.
            if waited > a.timeout:
                return fail(f"timed out after {waited:.0f}s", captured=server.counts)
            if server.total and time.time() - server.last > 120:
                return fail("no frame for 120s; the page stopped sending", captured=server.counts)
            time.sleep(0.25)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        server.shutdown()
        shutil.rmtree(profile, ignore_errors=True)

    wall = round(time.time() - t0, 1)
    record = {
        "ok": True, "frames": a.frames, "size": a.size, "fps": a.fps, "passes": passes,
        "start_yaw": a.start_yaw, "arc": a.arc, "yaws": yaws or None, "rotate": a.rotate or "0,0,0",
        "elevation": a.elevation, "color": a.color,
        "captured": server.counts, "capture_s": wall,
        "seconds": round(a.frames / a.fps, 2), "renderer": "webgl",
    }

    if not a.no_video and "beauty" in passes:
        video = out / a.video_name
        ok, why = encode(out / "beauty", video, a.fps)
        if not ok:
            return fail(f"ffmpeg failed: {why}", captured=server.counts)
        record["video"] = video.name
        record["video_bytes"] = video.stat().st_size

    (out / "video.json").write_text(json.dumps(record, indent=2))
    print(json.dumps(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
