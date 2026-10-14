#!/usr/bin/env python3
"""/dapilot:quickstart: set this computer up, check the required accounts, start the run.

    quickstart.py [file or folder]

Standard library only, so it runs wherever Python 3 does: macOS, Linux, Windows. What it prints is
what a person reads, so it says little: the wordmark, one line per setup item, the connection table,
and one sentence about what happens next. Details go to the scripts' own logs.

1. The store, seeded from the plugin's templates when new: the env file, the config, the pricing policy.
2. The environment (setup_env.py): a Python with the pipeline's packages, Blender, Chrome, ffmpeg,
   found or installed, their paths written to the store's .env. From a checkout on macOS or Linux,
   scripts/dev/shop.sh setup then adds the console's packages and the Etsy taxonomy.
3. The required accounts (3DAPI, Etsy, fal), each checked against its platform. Optional ones such as
   Instagram do not block. Anything required and not connected ends the run here: the person connects
   it with /dapilot:config and runs quickstart again. The page is not opened from here.
4. With a file or folder given and everything connected, the intake run starts on it in the
   background (run_intake.py) and this script says so. It stops at the price for the person to approve.

Exit 0 when the run started or everything is ready, 1 when a required account is not connected,
2 when the file or folder does not exist.
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

TEMPLATES = HERE.parent / "templates"


def run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, stdin=subprocess.DEVNULL, text=True, **kw)


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
    run([sys.executable, str(HERE / "setup_env.py")],
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
    if code in (0, 2):   # 2: setup stopped at the empty credentials, which the page fills in
        print(f"Checkout ready ({secs} s).")
    else:
        print(f"Checkout set up with notes ({secs} s)." + (f" Details: {log}" if log else ""))


def check_accounts() -> list[str]:
    """Verify every account against its platform and print the table. Returns the required ones not connected."""
    run([sys.executable, str(HERE / "integrate.py"), "test"], capture_output=True)
    run([sys.executable, str(HERE / "integrate.py"), "status"])
    r = run([sys.executable, str(HERE / "integrate.py"), "status", "--json"], capture_output=True)
    try:
        rows = json.loads(r.stdout)["integrations"]
    except (ValueError, KeyError):
        return ["the connection check"]
    return [i["name"] for i in rows if i["required"] and i["status"] != "done"]


def start_run(root: Path, store: Path, target: Path) -> None:
    """The intake, in the background, logged under runs/. It stops at the price gate on its own."""
    run_id = time.strftime("quickstart-%Y%m%dT%H%M%S")
    log_dir = root / "runs" / run_id
    log_dir.mkdir(parents=True, exist_ok=True)
    py = os.environ.get("DAPILOT_PYTHON") or sys.executable
    with (log_dir / "director.log").open("a") as log:
        subprocess.Popen([py, str(HERE / "run_intake.py"), "--run", run_id, str(target)], cwd=root,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                         env={**os.environ, "DAPILOT_STORE_DIR": str(store), "DAPILOT_PROJECT_DIR": str(root)})
    what = target.name if target.is_file() else f"the {len([p for p in target.iterdir() if p.is_file()])} files in {target.name}"
    print(f"Started on {what}. It stops at the price for your approval.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", help="a model file, or a folder of them")
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)   # our lines and the children's come out in order through a pipe
    root = project_dir()
    store = store_dir()
    os.environ["DAPILOT_STORE_DIR"], os.environ["DAPILOT_PROJECT_DIR"] = str(store), str(root)
    target = Path(a.target).expanduser() if a.target else None
    if target and not target.exists():
        print(f"There is no file or folder called {a.target}.")
        return 2

    run([sys.executable, str(HERE / "integrate.py"), "banner"])
    print()
    setup(root, store)
    print()
    missing = check_accounts()
    print()
    if missing:
        print("Not connected: " + ", ".join(missing) + ". Connect " + ("it" if len(missing) == 1 else "them")
              + " with /dapilot:config, then run quickstart again.")
        return 1
    if target:
        start_run(root, store, target)
    else:
        print("Everything is connected. To list a model, run quickstart with the file or the folder.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
