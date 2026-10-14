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
import re
import os
import shutil
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store_path import store_dir as _store_dir  # noqa: E402  one rule for every script

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())["name"]
LEDGER = PLUGIN_ROOT / "scripts" / "ledger.py"
LAUNCH_RETRIES = 3          # for API errors before any work (529 and friends)
LAUNCH_PAUSE_S = 60


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def project_root() -> Path:
    return Path(os.environ.get("DAPILOT_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()


def env_value(raw: str) -> str:
    """A .env value the way bash's `source` reads it: quoted up to its closing quote, else cut at ` #`.
    A trailing comment on FAL_API_KEY went to fal as part of the key (2026-09-22); bash had dropped it."""
    v = raw.strip()
    if v[:1] in "\"'" and v[:1] and v.find(v[0], 1) > 0:
        return v[1:v.find(v[0], 1)]
    if v.startswith("#"):  # `KEY=   # comment` is an empty value, not a comment as the value
        return ""
    return re.split(r"\s#", v, maxsplit=1)[0].strip()


def load_env(root: Path) -> None:
    """The store's .env: KEY=VALUE lines. Never overrides what the shell already set.
    The store is resolved once here and exported, so every worker and script sees the same one."""
    store = _store_dir()
    os.environ["DAPILOT_STORE_DIR"] = str(store)
    env_file = store / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip().removeprefix("export ").strip(), env_value(v)
        if v and k not in os.environ:
            os.environ[k] = v


def load_config(root: Path) -> dict:
    store = _store_dir()
    for p in (store / "config.json", Path(__file__).resolve().parent.parent / "templates" / "config.example.json"):
        if p.exists():
            return json.loads(p.read_text())
    raise SystemExit(f"no config.json in {store} and no template in the plugin")


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


def worker_budget(brief: dict, a, cfg: dict) -> float:
    """Per-stage budget from config `budget.usd_per_stage`, else the per-worker default."""
    per_stage = cfg.get("budget", {}).get("usd_per_stage", {})
    return float(per_stage.get(brief["stage"], a.budget_per_worker))


def worker_turns(brief: dict, a, cfg: dict) -> int:
    """Per-stage turn cap from config `turns_per_stage`, else --max-turns. Research needs about 20 tool calls."""
    return int(cfg.get("turns_per_stage", {}).get(brief["stage"], a.max_turns))


def worker_minutes(brief: dict, cfg: dict) -> int:
    """Wall clock for one worker: `minutes_per_stage` for its stage, else `max_worker_minutes`."""
    return int((cfg.get("minutes_per_stage") or {}).get(brief["stage"], cfg.get("max_worker_minutes", 15)))


def worker_cmd(brief: dict, a, cfg: dict) -> list[str]:
    # A stage may name its own worker (design runs product-designer); else the department's.
    agent = brief.get("agent") or cfg["departments"][brief["department"]]["worker"]
    return [
        "claude", "-p",
        "--agent", f"{PLUGIN_NAME}:{agent}",
        "--plugin-dir", str(PLUGIN_ROOT),
        "--dangerously-skip-permissions",
        "--max-turns", str(worker_turns(brief, a, cfg)),
        "--max-budget-usd", str(worker_budget(brief, a, cfg)),
        "--output-format", "json",
        json.dumps(brief),
    ]


def run_headless(cmd: list[str], cwd: Path, timeout_s: int) -> tuple[int | None, str, str, bool]:
    """Run a worker in its own process group and kill the whole group on timeout.

    subprocess.run's timeout only kills the direct child; `claude -p` leaves
    tool subprocesses holding the pipes, so the wait outlived the timeout by
    an hour on 2026-09-09. Returns (returncode, stdout, stderr, timed_out).
    """
    # Headless workers see an empty CLAUDE_PLUGIN_ROOT (2026-09-13), so every skill's
    # `${CLAUDE_PLUGIN_ROOT}/scripts/...` line failed and the worker went looking for
    # the scripts by hand. Set it here; an absolute work dir spares the same hunt.
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    # A Bash call with no `timeout` gets 120 s, then Claude Code moves it to the background.
    # Headless, the worker can only end its turn to wait, which ends the process: the design
    # worker of 2026-09-22 lost its produce call that way. 10 min covers produce's 480 s deadline.
    env.setdefault("BASH_DEFAULT_TIMEOUT_MS", "600000")
    env.setdefault("BASH_MAX_TIMEOUT_MS", "600000")
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         start_new_session=True, env=env)
    try:
        out, err = p.communicate(timeout=timeout_s)
        return p.returncode, out, err, False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            out, err = p.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        return None, out or "", err or "", True


def keep_worker_output(root: Path, brief: dict, cmd: list[str], rc, out: str, err: str, meta: dict) -> None:
    """Every worker's raw output goes to runs/<run>/workers/, so a run can be read back later."""
    d = root / "runs" / brief["run"] / "workers"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{brief['item'][:8]}-{brief['stage']}-{brief['attempt']}.json"
    f.write_text(json.dumps({"brief": brief, "cmd": cmd, "returncode": rc, "meta": meta,
                             "stdout": out[-200000:], "stderr": err[-20000:]}, indent=2))


def run_script_stage(brief: dict, root: Path, timeout_s: int) -> tuple[dict, dict]:
    """A stage with no judgement in it: run its script, and write the result line a worker would have printed.

    The script takes --workdir, prints one JSON object and exits 0 when the stage is done. No model is
    spent, so the cost is zero and the only budget is the wall clock.
    """
    tag = f"{brief['item'][:8]} {brief['stage']} #{brief['attempt']}"
    wd = Path(brief["workdir"]) if Path(brief["workdir"]).is_absolute() else root / brief["workdir"]
    lock = wd / ".lock"
    lock.write_text(json.dumps({"run": brief["run"], "stage": brief["stage"], "agent": "run_intake"}))
    cmd = [os.environ.get("DAPILOT_PYTHON") or sys.executable, str(PLUGIN_ROOT / "scripts" / brief["script"]), "--workdir", str(wd)]
    t0 = time.time()
    rc, stdout, stderr, timed_out = run_headless(cmd, root, timeout_s)
    meta = {"usd": 0.0, "tokens": 0, "turns": 0, "seconds": round(time.time() - t0, 1), "launch_attempts": 1}
    keep_worker_output(root, brief, cmd, rc, stdout, stderr, meta)
    lock.unlink(missing_ok=True)
    out = last_json_line(stdout) or last_json_line(stderr) or {}
    ok = rc == 0 and out.get("ok") is not False and all(list(wd.glob(g)) for g in brief["produces"])
    reason = "" if ok else (f"{brief['script']} exceeded {timeout_s}s" if timed_out else
                            str(out.get("reason") or out.get("error") or (stderr or stdout)[-200:] or f"exit {rc}"))
    summary = json.dumps({k: v for k, v in out.items() if k in ("etsy", "posted", "failed", "skipped", "cached")}) if ok else reason
    log(f"{tag}: script {'ok' if ok else 'failed: ' + reason[:120]}")
    return {"ok": ok, "cached": bool(out.get("cached")), "run": brief["run"], "item": brief["item"], "stage": brief["stage"],
            "agent": "run_intake", "department": brief["department"], "outputs": brief["produces"] if ok else [],
            "needs_human": None, "reason": reason, "summary": summary[:300]}, meta


def run_worker(brief: dict, a, cfg: dict, root: Path, timeout_s: int) -> tuple[dict, dict]:
    """Return (result_line, meta). Always returns a result line the ledger can record."""
    if brief.get("script"):
        return run_script_stage(brief, root, timeout_s)
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
        rc, stdout, stderr, timed_out = run_headless(cmd, root, timeout_s)
        meta["seconds"] = round(time.time() - t0, 1)
        if timed_out:
            synthetic["reason"] = f"worker exceeded {timeout_s}s wall clock; process group killed"
            log(f"{tag}: timeout after {timeout_s}s")
            keep_worker_output(root, brief, cmd, rc, stdout, stderr, meta)
            return synthetic, meta
        try:
            out = json.loads(stdout)
        except json.JSONDecodeError:
            synthetic["reason"] = f"claude -p exit {rc}, non-JSON output: {(stderr or stdout)[:200]}"
            log(f"{tag}: {synthetic['reason']}")
            keep_worker_output(root, brief, cmd, rc, stdout, stderr, meta)
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
        keep_worker_output(root, brief, cmd, rc, stdout, stderr, meta)
        if out.get("is_error") or result is None:
            synthetic["reason"] = f"worker ended without a result line: {str(out.get('result', ''))[:200]}"
            log(f"{tag}: {synthetic['reason']}")
            return synthetic, meta
        return result, meta
    synthetic["reason"] = "API unavailable after retries"
    return synthetic, meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", help="model files to take in; optional with --items or --resume")
    ap.add_argument("--items", help="comma-separated work-dir hashes to run, no folder needed")
    ap.add_argument("--resume", action="store_true", help="run every work dir with an enabled stage still to do")
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
        # Claude's installer puts it in ~/.local/bin, which a service or a fresh tmux shell may not have on PATH;
        # workers inherit whatever this finds
        for d in (Path.home() / ".local" / "bin", Path.home() / ".claude" / "local"):
            if (d / "claude").exists():
                os.environ["PATH"] = f"{d}{os.pathsep}{os.environ.get('PATH', '')}"
                break
    if not shutil.which("claude"):
        print(json.dumps({"ok": False, "reason": f"claude CLI not on PATH ({os.environ.get('PATH')}) nor in ~/.local/bin"})); return 2
    if a.billing == "subscription":
        os.environ.pop("ANTHROPIC_API_KEY", None)
    if a.billing == "api" and not os.environ.get("ANTHROPIC_API_KEY"):
        print(json.dumps({"ok": False, "reason": "--billing api but ANTHROPIC_API_KEY is not set"})); return 2
    billing = "API key" if os.environ.get("ANTHROPIC_API_KEY") else "subscription (logged-in account)"
    a.max_workers = a.max_workers or dept.get("max_workers") or cfg.get("max_workers_per_lead", 3)
    a.budget_per_worker = a.budget_per_worker or cfg.get("budget", {}).get("usd_per_item", 0.5)
    ceiling = cfg.get("budget", {}).get("usd_per_run")
    run = a.run or f"intake-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

    if not (a.folder or a.items or a.resume):
        print(json.dumps({"ok": False, "reason": "give a folder, --items or --resume"})); return 2
    init_args = ["init", "--run", run, "--department", a.department]
    if a.folder:
        init_args += ["--folder", a.folder]
    if a.items:
        init_args += ["--items", a.items]
    if a.resume:
        init_args += ["--resume"]
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
        nxt = ledger("next", "--run", run, "--max", str(a.max_workers), *(["--peek"] if a.dry_run else []))
        briefs = nxt.get("briefs", [])
        if a.dry_run:
            for b in briefs:
                if b.get("script"):
                    print(f"python {b['script']} --workdir {b['workdir']}   # script stage '{b['stage']}', no worker")
                    continue
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
            # per worker, not per run: one stuck worker must not hold an item for the run's whole budget
            futures = {pool.submit(run_worker, b, a, cfg, root, worker_minutes(b, cfg) * 60): b for b in briefs}
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
