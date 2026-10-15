#!/usr/bin/env python3
"""The listener: what the pipeline does, one line per event, for the whole session.

    watch_run.py [--root DIR] [--once]

Runs as the plugin's monitor (monitors/monitors.json) in the session's working directory, which
is where quickstart's director keeps work/ and runs/. It prints one plain line each time something
happens and nothing when nothing does, because every line it prints reaches the model as a
notification and the model reacts to it:

    dapilot: cat.3mf: research done (4 min). identity low: cat-shaped lidded trinket box ...
    dapilot: cat.3mf is waiting for a sale price: proposed 3.99 USD, floor 3.99 USD. Ask the person now ...
    dapilot: cat.3mf: design blocked. design stopped at stills: no studio page ...
    dapilot: run quickstart-2026... finished: 5 workers, 0.55 USD, 10 min.

What it watches: new lines in every runs/<id>/director.log (a run that starts later is picked up),
and work/<hash>/price.json appearing without price.approved.json, which is the gate. Lines written
before the monitor started are not replayed, except a gate that is already open when it starts,
which is said once. Standard library only. --once prints the current state and exits (a check).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
from pathlib import Path

POLL_S = 2.0
RESULT = re.compile(r"^\[\d\d:\d\d:\d\d\] (?P<hash>[0-9a-f]{8}) (?P<stage>\w+)#(?P<attempt>\d+) -> (?P<status>\w+)(?: \| (?P<secs>[\d.]+)s, (?P<turns>\d+) turns, (?P<usd>[\d.]+) USD)?(?: \| (?P<summary>.*))?$")
DISPATCH = re.compile(r"^\[\d\d:\d\d:\d\d\] pass \d+: dispatching \d+ worker\(s\): (?P<list>.*)$")
DONE = re.compile(r"^\[\d\d:\d\d:\d\d\] done: (?P<counts>\{.*?\}), (?P<workers>\d+) workers, (?P<usd>[\d.]+) USD, (?P<secs>[\d.]+)s")
START = re.compile(r"^\[\d\d:\d\d:\d\d\] run (?P<run>\S+): (?P<items>\d+) items")


def say(line: str) -> None:
    sys.stdout.write("dapilot: " + line.rstrip() + "\n")
    sys.stdout.flush()


def read(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


class Watcher:
    def __init__(self, root: Path):
        self.root = root
        self.offsets: dict[Path, int] = {}
        self.names: dict[str, str] = {}
        self.gates_said: set[str] = set()
        self.started = time.time()
        self.alive = root / "runs" / ".monitor.alive"   # rewritten every poll; approve_price.py reports it as monitor_alive

    def beat(self) -> None:
        try:
            self.alive.parent.mkdir(parents=True, exist_ok=True)
            self.alive.write_text(json.dumps({"pid": os.getpid(), "root": str(self.root), "at": time.time()}))
        except OSError:
            pass

    # ---- names
    def name(self, h8: str) -> str:
        if h8 in self.names:
            return self.names[h8]
        work = self.root / "work"
        if work.is_dir():
            for wd in work.iterdir():
                if wd.name.startswith(h8):
                    meta = read(wd / "meta.json") or {}
                    self.names[h8] = meta.get("file") or h8
                    return self.names[h8]
        return h8

    # ---- director logs
    def logs(self) -> list[Path]:
        runs = self.root / "runs"
        if not runs.is_dir():
            return []
        return [p / "director.log" for p in runs.iterdir() if (p / "director.log").is_file()]

    def tail(self, log: Path, replay: bool) -> None:
        try:
            size = log.stat().st_size
        except OSError:
            return
        if log not in self.offsets:
            self.offsets[log] = 0 if replay else size   # a log that exists at start is not replayed
            if not replay:
                return
        if size < self.offsets[log]:
            self.offsets[log] = 0
        if size == self.offsets[log]:
            return
        with log.open("r", errors="replace") as f:
            f.seek(self.offsets[log])
            chunk = f.read()
            self.offsets[log] = f.tell()
        for line in chunk.splitlines():
            self.line(log.parent.name, line)

    def line(self, run: str, line: str) -> None:
        m = START.match(line)
        if m:
            say(f"run {m['run']} started on {m['items']} model{'s' if m['items'] != '1' else ''}.")
            return
        m = DISPATCH.match(line)
        if m:
            parts = []
            for tok in m["list"].split(", "):
                h8, _, rest = tok.partition("/")
                parts.append(f"{self.name(h8)}: {rest.split('#')[0]}")
            say("working on " + "; ".join(parts) + ".")
            return
        m = RESULT.match(line)
        if m:
            took = f" ({int(float(m['secs']) // 60)} min {int(float(m['secs']) % 60)} s)" if m["secs"] else ""
            summary = (m["summary"] or "").strip()
            status = m["status"]
            if status == "done":
                say(f"{self.name(m['hash'])}: {m['stage']} done{took}." + (f" {summary}" if summary else ""))
            elif status == "blocked":
                say(f"{self.name(m['hash'])}: {m['stage']} blocked. {summary} A person clears it (delete the blocked file or Try again in the console); nothing else runs for this model until then.")
            else:
                say(f"{self.name(m['hash'])}: {m['stage']} {status}." + (f" {summary}" if summary else "") + " The run retries it once; a second failure is final.")
            return
        m = DONE.match(line)
        if m:
            say(f"run {run} finished: {m['workers']} workers, {float(m['usd']):.2f} USD, {int(float(m['secs']) // 60)} min. "
                "If a model is waiting for a sale price it is listed next; otherwise the models that passed the gate are being designed, written and published on their own.")
            return

    # ---- the gate
    def gates(self) -> None:
        work = self.root / "work"
        if not work.is_dir():
            return
        for wd in work.iterdir():
            if wd.name in self.gates_said or not (wd / "price.json").exists():
                continue
            if (wd / "price.approved.json").exists() or (wd / "design.blocked.json").exists() or (wd / ".lock").exists():
                continue
            p = read(wd / "price.json") or {}
            c = read(wd / "price.candidates.json") or {}
            meta = read(wd / "meta.json") or {}
            cur = c.get("currency") or p.get("currency") or "USD"
            self.gates_said.add(wd.name)
            say(f"{meta.get('file') or wd.name[:8]} is waiting for a sale price: proposed {p.get('price')} {cur}, floor {c.get('floor', p.get('floor'))} {cur}"
                f"{', ' + str(p.get('candidate')) + ' candidate' if p.get('candidate') else ''}. "
                "This is the one approval a person gives: ask them now with the /dapilot:review procedure "
                "(approve_price.py pending, one AskUserQuestion with the candidates, then approve or decline). Do not choose for them.")

    def once(self) -> None:
        for log in self.logs():
            self.tail(log, replay=False)
        self.gates()

    def run(self) -> None:
        for log in self.logs():          # mark what exists; no replay
            self.tail(log, replay=False)
        self.gates()                     # a gate already open is said once
        self.beat()
        try:
            while True:
                time.sleep(POLL_S)
                for log in self.logs():
                    self.tail(log, replay=(log not in self.offsets))   # a new run's log is read from its start
                self.gates()
                self.beat()
        finally:
            try:
                self.alive.unlink()
            except OSError:
                pass


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows pipes default to cp1252
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.environ.get("DAPILOT_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    ap.add_argument("--once", action="store_true", help="say what is open now and exit")
    a = ap.parse_args()
    w = Watcher(Path(a.root).resolve())
    if a.once:
        w.gates()
        return 0
    # the session ends the monitor with a signal; turn it into a clean exit so the alive file goes with us
    for sig in (signal.SIGTERM, signal.SIGINT) + ((signal.SIGHUP,) if hasattr(signal, "SIGHUP") else ()):
        signal.signal(sig, lambda *_: sys.exit(0))
    w.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
