#!/usr/bin/env python3
"""Capture a studio turntable from the browser scene and encode it.

  capture_turntable.py --workdir DIR [--frames 120] [--size 720] [--fps 24]
                       [--passes beauty,depth,normal] [--start-yaw 35] [--no-video]
                       [--out DIR/renders] [--rotate x,y,z] [--elevation 18]
  capture_turntable.py --workdir DIR --yaws 20,110,200,290 --size 2048 --passes beauty --no-video
  capture_turntable.py --workdir DIR --mode draft [--frames 8] [--size 512]

Writes OUT/<pass>/frame_0000.png ... , OUT/turntable.mp4 and a record at
OUT/video.json. OUT defaults to DIR/renders. Prints one JSON object. Exit 0 on
success.

--mode draft is the preview stage: --frames evenly spaced yaws from 0, beauty
only, no video, written as DIR/renders/draft_00.png onward with a `draft`
record merged into DIR/renders/render.json. The file as exported: no --rotate,
no --color. Replaced the Blender draft turntable on 2026-10-20.

Yaw N shows the side the draft at yaw N shows. --rotate is the orientation in
degrees about the file's own x, y, z. --yaws captures exactly those angles, one
frame each, which is what product stills want.

Why a browser: WebGL draws a frame in milliseconds on any machine, with
software rendering where there is no GPU, so eight drafts cost seconds and a
rotation under a minute. The depth and normal passes come off the same scene
for one extra draw each, and they are what a diffusion model conditions on.

How it works: this script serves a collector on a loopback port, opens the
studio page headless with capture=1 pointing at it, and writes every frame the
page posts. The page owns the stepping because only it knows when a frame is
truly on the canvas; this script owns the disk and the encode.

The studio page: --studio (DAPILOT_STUDIO_URL, default the console's dev
server) is used when it answers. When nothing does, this script serves the
page itself from --studio-dir, the built copy the plugin ships at
plugins/dapilot/studio, on a loopback port for the length of the run. An
installed plugin therefore needs no console, no npm and no Blender for any
render: Chrome alone, plus ffmpeg for a video.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
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
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PASSES = ("beauty", "depth", "normal")
HERE = Path(__file__).resolve().parent


def bundled_studio() -> Path:
    """The built studio page the plugin ships: DAPILOT_STUDIO_DIR, else <plugin root>/studio."""
    if os.environ.get("DAPILOT_STUDIO_DIR"):
        return Path(os.environ["DAPILOT_STUDIO_DIR"])
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    return (Path(root) if root else HERE.parent) / "studio"


class StaticStudio(ThreadingHTTPServer):
    """Serves the built studio page on a loopback port for one run, when no console is answering."""

    daemon_threads = True

    def __init__(self, directory: Path):
        super().__init__(("127.0.0.1", 0), functools.partial(StaticHandler, directory=str(directory)))

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.socket.getsockname()[1]}"


class StaticHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        # A single-page app: /studio and anything else that is not a file on disk is index.html.
        if not Path(self.translate_path(self.path)).is_file():
            self.path = "/index.html"
        super().do_GET()

    def end_headers(self) -> None:
        self.send_header("cache-control", "no-store")
        super().end_headers()

    def log_message(self, *_args) -> None:
        return


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

    def __init__(self, out: Path, source: Path, index_map: list[int] | None = None):
        super().__init__(("127.0.0.1", 0), Handler)
        self.out = out
        # the page numbers the frames it was asked for from 0; on a resume those are only the missing ones
        self.index_map = index_map
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
            n = server.index_map[int(index)] if server.index_map is not None else int(index)
            (target / f"frame_{n:04d}.png").write_bytes(body)
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
    ap.add_argument("--mode", choices=["turntable", "draft"], default="turntable",
                    help="draft: the preview stage's frames, DIR/renders/draft_NN.png, beauty only, no video")
    ap.add_argument("--frames", type=int, default=None, help="default 120, or 8 in draft mode")
    ap.add_argument("--size", type=int, default=None, help="square output in px; default 720, or 512 in draft mode")
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
    ap.add_argument("--studio", default=os.environ.get("DAPILOT_STUDIO_URL") or None,
                    help="a studio page already running (the console's dev server) to use instead of the bundled page; "
                         "DAPILOT_STUDIO_URL sets it too. Unset, the plugin's own built page is served for the run")
    ap.add_argument("--studio-dir", default=None,
                    help="the built studio page to serve; default DAPILOT_STUDIO_DIR, else the plugin's studio/")
    ap.add_argument("--no-video", action="store_true", help="frames only, skip ffmpeg")
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--resume", action="store_true",
                    help="keep frames already captured with the same settings, capture only the missing ones, "
                         "and exit 3 (partial) instead of failing when --timeout runs out")
    a = ap.parse_args()

    draft = a.mode == "draft"
    if a.frames is None:
        a.frames = 8 if draft else 120
    if a.size is None:
        a.size = 512 if draft else 720
    if draft:
        if a.yaws or a.rotate or a.color or a.out:
            return fail("--mode draft takes no --yaws, --rotate, --color or --out: drafts are the file as exported, in DIR/renders")
        a.passes, a.no_video = "beauty", True
        a.yaws = ",".join(f"{360.0 * i / a.frames:g}" for i in range(a.frames))   # 45 degree steps from the front
        if a.elevation is None:
            a.elevation = 18.0

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
    # The page: the plugin's own built copy, served here for the run, unless a running one was named.
    # Not "whatever answers on a port": another app on 8081 once passed the liveness check and the
    # capture waited 45 s for a model fetch that never came (2026-10-20).
    static: StaticStudio | None = None
    if a.studio:
        base = a.studio.rstrip("/")
        if not page_is_up(base):
            return fail(f"no studio page at {base}: start the console with `npm run dev` in frontend/, or drop --studio "
                        "and DAPILOT_STUDIO_URL to use the plugin's own page")
    else:
        studio_dir = Path(a.studio_dir).resolve() if a.studio_dir else bundled_studio()
        if not (studio_dir / "index.html").is_file():
            return fail(f"no built studio page at {studio_dir}: run scripts/dev/build_studio.sh from a checkout, "
                        "or point --studio at a running console")
        static = StaticStudio(studio_dir)
        threading.Thread(target=static.serve_forever, daemon=True).start()
        base = static.url

    out = Path(a.out).resolve() if a.out else wd / "renders"
    if draft:
        out = wd / "renders" / ".draft"   # the capture's own layout; the frames move up to renders/draft_NN.png at the end
    out.mkdir(parents=True, exist_ok=True)
    # every angle of the sequence, as the studio page computes them (lib/model/capture.ts)
    all_yaws = yaws or [(a.start_yaw - a.arc / 2 + a.arc * i / max(a.frames - 1, 1) + 360) % 360 for i in range(a.frames)]
    # By content, not mtime: produce rebuilds display.ply on every call, and an mtime key threw away
    # every frame each time (2026-09-22, the shark restarted at frame 0 roughly every 60 frames).
    digest = hashlib.sha256(sources[0].read_bytes()).hexdigest()
    settings = {"frames": a.frames, "size": a.size, "passes": passes, "yaws": [round(y, 6) for y in all_yaws],
                "rotate": a.rotate, "elevation": a.elevation, "color": a.color,
                "source": [sources[0].name, digest]}
    manifest = out / "capture.json"
    todo = list(range(a.frames))
    if a.resume and manifest.exists() and json.loads(manifest.read_text() or "{}") == settings:
        todo = [i for i in todo if not all((out / n / f"frame_{i:04d}.png").exists() for n in passes)]
        print(f"resuming: {a.frames - len(todo)} of {a.frames} frames already captured", file=sys.stderr)
    else:
        for name in passes:
            for stale in (out / name).glob("frame_*.png"):
                stale.unlink()                        # a rerun must not blend two sequences
        manifest.write_text(json.dumps(settings))
    resumed = len(todo) < a.frames

    server = Collector(out, sources[0], todo if resumed else None)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    model_url = f"http://127.0.0.1:{server.port}/model{sources[0].suffix}"
    if resumed:   # ask only for the missing angles, exactly as they were computed the first time
        yaws = [all_yaws[i] for i in todo]
    page = f"{base}/studio?" + urllib.parse.urlencode({
        "model": model_url, "capture": "1", "frames": len(todo), "size": a.size,
        "passes": ",".join(passes), "startYaw": a.start_yaw, "arc": a.arc,
        **({"yaws": ",".join(f"{y:.6f}".rstrip("0").rstrip(".") for y in yaws)} if yaws else {}),
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
    proc = None if not todo else subprocess.Popen(
        [browser, "--headless=new", "--disable-gpu-sandbox", "--hide-scrollbars", *linux,
         "--window-size=1400,1400", f"--user-data-dir={profile}", page],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    expected = len(todo) * len(passes)
    t0 = time.time()

    def partial(why: str) -> int:
        """Out of time on a resumable run: what is on disk stays, and the next call picks up from there."""
        have = sum(all((out / n / f"frame_{i:04d}.png").exists() for n in passes) for i in range(a.frames))
        print(json.dumps({"ok": False, "partial": True, "reason": why, "frames_done": have, "frames": a.frames,
                          "captured": server.counts}))
        return 3

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
                if a.resume:
                    return partial(f"timed out after {waited:.0f}s")
                return fail(f"timed out after {waited:.0f}s", captured=server.counts)
            if server.total and time.time() - server.last > 120:
                return fail("no frame for 120s; the page stopped sending", captured=server.counts)
            time.sleep(0.25)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        server.shutdown()
        if static is not None:
            static.shutdown()
        shutil.rmtree(profile, ignore_errors=True)

    wall = round(time.time() - t0, 1)
    studio_used = "bundled" if static is not None else base
    if draft:
        # The preview stage's contract: renders/draft_00.png onward and a `draft` record in renders/render.json,
        # the files every later stage reads to see what the object is.
        renders = wd / "renders"
        outputs = []
        for i, yaw in enumerate(all_yaws):
            (out / "beauty" / f"frame_{i:04d}.png").replace(renders / f"draft_{i:02d}.png")
            outputs.append({"file": f"draft_{i:02d}.png", "yaw": round(yaw, 3)})
        shutil.rmtree(out, ignore_errors=True)
        rec_p = renders / "render.json"
        rec = json.loads(rec_p.read_text() or "{}") if rec_p.exists() else {}
        rec["draft"] = {"renderer": "webgl", "frames": a.frames, "res": a.size, "elevation": a.elevation,
                        "outputs": outputs, "wall_s": wall, "studio": studio_used}
        rec_p.write_text(json.dumps(rec, indent=2))
        print(json.dumps({"ok": True, "mode": "draft", **rec["draft"]}))
        return 0

    record = {
        "ok": True, "frames": a.frames, "size": a.size, "fps": a.fps, "passes": passes,
        "start_yaw": a.start_yaw, "arc": a.arc, "yaws": yaws or None, "rotate": a.rotate or "0,0,0",
        "elevation": a.elevation, "color": a.color,
        "captured": server.counts, "capture_s": wall, "resumed_from": a.frames - len(todo) if resumed else 0,
        "seconds": round(a.frames / a.fps, 2), "renderer": "webgl", "studio": studio_used,
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
