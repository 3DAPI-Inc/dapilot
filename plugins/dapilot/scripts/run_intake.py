#!/usr/bin/env python3
"""Deterministic director and lead for an intake run. No model turns on routing.

  run_intake.py <folder> [--run ID] [--department catalogue] [--max-workers N]
                [--max-turns 20] [--budget-per-worker USD] [--stages FILE] [--dry-run]

The loop the catalogue-lead agent describes, as a process:

  ledger init -> repeat { ledger next -> one worker per brief, in parallel
  -> ledger record each result with its exact cost } until no work is left,
  the spend ceiling is hit, or two passes change nothing -> ledger report.

A worker is `claude -p --agent dapilot:catalogue-worker` run headless with
the plugin loaded, the brief as its prompt, a turn cap and a dollar cap. It
gets exactly the tools and skills of its agent file and nothing else. There
is no parent session and no spawning. Only workers spend tokens.

Reads store/.env (ANTHROPIC_API_KEY, DAPI_*, DAPILOT_PYTHON) and
store/config.json. Prints progress on stderr and one JSON summary on stdout.
Exit 0 when nothing failed, 1 otherwise, 2 on usage or environment errors.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())["name"]
LEDGER = PLUGIN_ROOT / "scripts" / "ledger.py"
LAUNCH_RETRIES = 3          # for API errors before any work (529 and friends)
LAUNCH_PAUSE_S = 60


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def project_root() -> Path:
    return Path(os.environ.get("DAPILOT_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()


def load_env(root: Path) -> None:
    """store/.env: KEY=VALUE lines. Never overrides what the shell already set."""
    store = Path(os.environ.get("DAPILOT_STORE_DIR") or root / "store")
    env_file = store / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v and k not in os.environ:
            os.environ[k] = v


def load_config(root: Path) -> dict:
    store = Path(os.environ.get("DAPILOT_STORE_DIR") or root / "store")
    for name in ("config.json", "config.example.json"):
        if (store / name).exists():
            return json.loads((store / name).read_text())
    raise SystemExit(f"no config in {store}")


def ledger(*args: str, stdin: str | None = None) -> dict:
    cmd = [sys.executable, str(LEDGER), *args]
    p = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise SystemExit(f"ledger {args[0]} returned non-JSON: {p.stdout[:300]} {p.stderr[:300]}")
    return out


def last_json_line(text: str) -> dict | None:
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def worker_cmd(brief: dict, a, cfg: dict) -> list[str]:
    agent = cfg["departments"][brief["department"]]["worker"]
    return [
        "claude", "-p",
        "--agent", f"{PLUGIN_NAME}:{agent}",
        "--plugin-dir", str(PLUGIN_ROOT),
        "--dangerously-skip-permissions",
        "--max-turns", str(a.max_turns),
        "--max-budget-usd", str(a.budget_per_worker),
        "--output-format", "json",
        json.dumps(brief),
    ]


def run_worker(brief: dict, a, cfg: dict, root: Path, timeout_s: int) -> tuple[dict, dict]:
    """Return (result_line, meta). Always returns a result line the ledger can record."""
    tag = f"{brief['item'][:8]} {brief['stage']} #{brief['attempt']}"
    synthetic = {
        "ok": False, "cached": False, "run": brief["run"], "item": brief["item"], "stage": brief["stage"],
        "agent": "run_intake", "department": brief["department"], "outputs": [], "needs_human": None,
        "reason": "", "summary": "worker produced no result line",
    }
    meta = {"usd": 0.0, "tokens": 0, "turns": 0, "seconds": 0.0, "launch_attempts": 0}
    cmd = worker_cmd(brief, a, cfg)
    for attempt in range(1, LAUNCH_RETRIES + 1):
        meta["launch_attempts"] = attempt
        t0 = time.time()
        try:
            p = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            synthetic["reason"] = f"worker exceeded {timeout_s}s wall clock"
            log(f"{tag}: timeout after {timeout_s}s")
            return synthetic, meta
        meta["seconds"] = round(time.time() - t0, 1)
        try:
            out = json.loads(p.stdout)
        except json.JSONDecodeError:
            synthetic["reason"] = f"claude -p exit {p.returncode}, non-JSON output: {(p.stderr or p.stdout)[:200]}"
            log(f"{tag}: {synthetic['reason']}")
            return synthetic, meta
        usage = out.get("usage", {})
        meta["usd"] = float(out.get("total_cost_usd") or 0.0)
        meta["tokens"] = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0)) \
            + int(usage.get("cache_creation_input_tokens", 0)) + int(usage.get("cache_read_input_tokens", 0))
        meta["turns"] = out.get("num_turns", 0)
        status = out.get("api_error_status")
        if status and meta["usd"] == 0 and attempt < LAUNCH_RETRIES:
            log(f"{tag}: API error {status} before any work; retrying launch in {LAUNCH_PAUSE_S}s ({attempt}/{LAUNCH_RETRIES})")
            time.sleep(LAUNCH_PAUSE_S)
            continue
        result = last_json_line(str(out.get("result", "")))
        if out.get("is_error") or result is None:
            synthetic["reason"] = f"worker ended without a result line: {str(out.get('result', ''))[:200]}"
            log(f"{tag}: {synthetic['reason']}")
            return synthetic, meta
        return result, meta
    synthetic["reason"] = "API unavailable after retries"
    return synthetic, meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--run")
    ap.add_argument("--department", default="catalogue")
    ap.add_argument("--max-workers", type=int)
    ap.add_argument("--max-turns", type=int, default=20)
    ap.add_argument("--budget-per-worker", type=float)
    ap.add_argument("--stages")
    ap.add_argument("--dry-run", action="store_true", help="init and show the first pass of worker commands; launch nothing")
    ap.add_argument("--billing", choices=["auto", "api", "subscription"], default=os.environ.get("DAPILOT_BILLING", "auto"),
                    help="api: require ANTHROPIC_API_KEY; subscription: hide the key from workers so they use the logged-in account; auto: whatever is set")
    a = ap.parse_args()

    root = project_root()
    os.environ["DAPILOT_PROJECT_DIR"] = str(root)
    load_env(root)
    os.environ.setdefault("DAPILOT_PYTHON", sys.executable)
    cfg = load_config(root)
    dept = cfg["departments"].get(a.department)
    if not dept:
        print(json.dumps({"ok": False, "reason": f"unknown department {a.department}"})); return 2
    if not shutil.which("claude"):
        print(json.dumps({"ok": False, "reason": "claude CLI not on PATH"})); return 2
    if a.billing == "subscription":
        os.environ.pop("ANTHROPIC_API_KEY", None)
    if a.billing == "api" and not os.environ.get("ANTHROPIC_API_KEY"):
        print(json.dumps({"ok": False, "reason": "--billing api but ANTHROPIC_API_KEY is not set"})); return 2
    billing = "API key" if os.environ.get("ANTHROPIC_API_KEY") else "subscription (logged-in account)"
    a.max_workers = a.max_workers or dept.get("max_workers") or cfg.get("max_workers_per_lead", 3)
    a.budget_per_worker = a.budget_per_worker or cfg.get("budget", {}).get("usd_per_item", 0.5)
    ceiling = cfg.get("budget", {}).get("usd_per_run")
    timeout_s = int(cfg.get("max_wall_minutes", 60)) * 60
    run = a.run or f"intake-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

    init_args = ["init", "--run", run, "--folder", a.folder, "--department", a.department]
    if a.stages:
        init_args += ["--stages", a.stages]
    init = ledger(*init_args)
    if not init.get("ok"):
        print(json.dumps(init)); return 2
    log(f"run {run}: {init['items']} items, workers<= {a.max_workers}, budget/worker {a.budget_per_worker} USD, ceiling {ceiling}, billing: {billing}")

    t_start = time.time()
    spent = 0.0
    passes = 0
    stalls = 0
    dispatched_total = 0
    while True:
        passes += 1
        nxt = ledger("next", "--run", run, "--max", str(a.max_workers))
        briefs = nxt.get("briefs", [])
        if a.dry_run:
            for b in briefs:
                print(" ".join(repr(x) if " " in x else x for x in worker_cmd(b, a, cfg)))
            print(json.dumps({"ok": True, "dry_run": True, "run": run, "briefs": len(briefs), "remaining": nxt.get("remaining")}))
            return 0
        if not briefs:
            st = ledger("status", "--run", run)
            if not st.get("work_left"):
                break
            stalls += 1
            log(f"pass {passes}: nothing ready, work_left={st['work_left']} counts={st['counts']}")
            if stalls >= 2:
                log("two passes changed nothing; stopping")
                break
            time.sleep(5)
            continue
        stalls = 0
        log(f"pass {passes}: dispatching {len(briefs)} worker(s): " + ", ".join(f"{b['item'][:8]}/{b['stage']}#{b['attempt']}" for b in briefs))
        with ThreadPoolExecutor(max_workers=a.max_workers) as pool:
            futures = {pool.submit(run_worker, b, a, cfg, root, timeout_s): b for b in briefs}
            for fut in as_completed(futures):
                b = futures[fut]
                result, meta = fut.result()
                dispatched_total += 1
                spent += meta["usd"]
                rec = ledger("record", "--run", run, "--result", "-", "--tokens", str(meta["tokens"]),
                             "--usd", str(meta["usd"]), "--model", "worker", stdin=json.dumps(result))
                log(f"{b['item'][:8]} {b['stage']}#{b['attempt']} -> {rec.get('status') or rec.get('reason')}"
                    f" | {meta['seconds']}s, {meta['turns']} turns, {meta['usd']:.4f} USD | {result.get('summary', '')[:90]}")
        st = ledger("status", "--run", run)
        if st.get("over_budget"):
            log(f"spend {st['cost']['usd']} USD over ceiling {ceiling}; stopping")
            break
        if not st.get("work_left"):
            break

    rep = ledger("report", "--run", run)
    counts = rep["counts"]
    summary = {
        "ok": counts.get("failed", 0) == 0, "run": run, "report": rep["report"], "counts": counts,
        "workers_dispatched": dispatched_total, "passes": passes,
        "usd_workers": round(spent, 4), "usd_ledger": rep["cost"]["usd"],
        "seconds": round(time.time() - t_start, 1), "billing": billing,
    }
    log(f"done: {counts}, {dispatched_total} workers, {spent:.4f} USD, {summary['seconds']}s -> {rep['report']}")
    print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
