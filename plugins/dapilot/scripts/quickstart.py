#!/usr/bin/env python3
"""/dapilot:quickstart: set this computer up, show the 3DAPI wordmark, open the Integrations page.

    quickstart.py [--port 3106]

Standard library only, so it runs wherever Python 3 does: macOS, Linux, Windows. What it prints
is what a person reads, so it says little: the wordmark, one line per step, the page's address and
the connection table. Details go to the setup script's own log.

1. Setup, when this is a checkout of the repo on macOS or Linux: `scripts/dev/shop.sh setup`
   installs the tools it can (brew, apt, dnf), the Python venv and packages, the frontend packages
   and the store files, and asks nothing, so sign-ins are left to the page. On a fresh machine it
   stops at the empty credentials, which is expected: the page fills them in. An installed plugin,
   or Windows, has no setup step here: the page needs only Python.
2. The Integrations page (integrate.py serve), opened in the browser.
3. What is connected (integrate.py status).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from store_path import project_dir, store_dir  # noqa: E402
import integrate  # noqa: E402  our_page: whether the page is still being served

BUDGET = 570   # seconds for the whole run: the Bash tool allows 600


def run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, stdin=subprocess.DEVNULL, text=True, **kw)


TEMPLATES = HERE.parent / "templates"


def seed(store: Path) -> None:
    """A new store gets the plugin's templates: the env file to fill in, the config, the pricing policy.
    Nothing that exists is touched."""
    (store / "reference").mkdir(parents=True, exist_ok=True)
    for src, dst in ((".env.example", ".env"), ("config.example.json", "config.json"),
                     ("pricing-policy.example.json", "reference/pricing-policy.json")):
        s, d = TEMPLATES / src, store / dst
        if s.exists() and not d.exists():
            d.write_bytes(s.read_bytes())
            if dst == ".env":
                d.chmod(0o600)


def setup(root: Path, store: Path) -> None:
    seed(store)
    print("Setting up this computer: Python packages, Blender, Chrome, ffmpeg. The first run can take a few minutes.")
    # the environment, the same way everywhere: a venv under the store, the three tools found or installed,
    # their paths written to the store's .env. Its lines are the person's report.
    subprocess.run([sys.executable, str(HERE / "setup_env.py")], stdin=subprocess.DEVNULL,
                   env={**os.environ, "DAPILOT_STORE_DIR": str(store), "DAPILOT_PROJECT_DIR": str(root)})
    shop = root / "scripts" / "dev" / "shop.sh"
    if os.name == "nt" or not shop.exists():
        return   # an installed plugin, or Windows: nothing more to set up for the pipeline
    # a checkout on macOS or Linux also gets the console's packages, the Etsy taxonomy and the dev checks
    t = time.time()
    with tempfile.TemporaryFile("w+") as out:
        code = run(["bash", str(shop), "setup"], stdout=out, stderr=subprocess.STDOUT,
                   env={**os.environ, "DAPILOT_STORE_DIR": str(store), "DAPILOT_PROJECT_DIR": str(root)}).returncode
        out.seek(0)
        text = out.read()
    secs = int(time.time() - t)
    m = re.search(r"runs/shop-[0-9T]+\.log", text)
    log = f"{root / m.group(0)}" if m else ""
    if code == 0:
        print(f"Checkout ready ({secs} s).")
    elif code == 2:   # setup stops at the empty credentials: the page fills them in
        print(f"Checkout ready ({secs} s). Credentials are next: fill them in on the page.")
    else:
        print(f"Checkout set up with notes ({secs} s)." + (f" Details: {log}" if log else ""))


def serve(port: int) -> str | None:
    """Open the page; returns its URL, or None when it could not start."""
    r = run([sys.executable, str(HERE / "integrate.py"), "serve", "--port", str(port)], capture_output=True)
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        res = {}
    if res.get("url"):
        print(f"Your Integrations page is open: {res['url']}")
        print("Fill in each account there, then close the page or press Done.")
        return res["url"]
    reason = res.get("reason") or (r.stderr or "").strip()[-160:]
    print("The page could not start" + (f": {reason}" if reason else "") + f". Try: quickstart.py --port {port + 6}")
    return None


def wait_for_page(url: str, deadline: float) -> bool:
    """True once the page has been closed (Done, or the tab closed); False when the time budget ran out first."""
    port = int(url.rsplit(":", 1)[1].rstrip("/"))
    while time.time() < deadline:
        if integrate.our_page(port) is None:
            return True
        time.sleep(2)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=3106)
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)   # our lines and the children's come out in order through a pipe
    root = project_dir()
    store = store_dir()
    os.environ["DAPILOT_STORE_DIR"], os.environ["DAPILOT_PROJECT_DIR"] = str(store), str(root)

    started = time.time()
    run([sys.executable, str(HERE / "integrate.py"), "banner"])
    print()
    setup(root, store)
    print()
    url = serve(a.port)
    print()
    if url and not wait_for_page(url, started + BUDGET):
        print("The page is still open. Say when you are done and the connections will be checked again.")
        return 0
    run([sys.executable, str(HERE / "integrate.py"), "status"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
