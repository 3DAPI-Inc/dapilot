#!/usr/bin/env python3
"""The price gate, for a Claude Code session: the one approval a person gives.

    approve_price.py pending [--wait SECONDS]                       items waiting at the gate, as JSON
    approve_price.py approve --workdir W [--price P] [--by NAME] [--reason TEXT] [--no-start]
    approve_price.py decline --workdir W [--by NAME] [--reason TEXT]

`pending` lists every work dir under <project>/work whose price stage has written a proposal
(price.json) that no person has answered yet: the proposal, the candidates the script computed,
the floor, the pack size. The model turns that into one question per item; the person clicks a
candidate or types a price. It also says what is `running` (items a live director holds, with the
stage), which directors are alive (runs/<id>/director.alive, touched by run_intake.py every few
seconds), what is `before_price` with no director (stopped; quickstart restarts it), and what was
`rejected` or `declined`. With --wait it polls until something is pending, or until no director is
alive and nothing runs, or until the seconds run out. A live director is never a stall, however
long its worker is silent: the slicer and fal take minutes and write nothing meanwhile.

`approve` is the console's POST /jobs/{id}/price without the console. The price is one of the
candidates or any number a person typed. A typed price is checked by price_candidates.py --check and
refused below the floor, for any author. It writes price.approved.json, files the product record,
and starts a director on the item so design, copy and publish run from that approval. Nothing here
chooses a price: the numbers come from price.json and price.candidates.json, the choice from the
person, the floor from the policy.

`decline` writes design.blocked.json with the person's reason, so the item stops there and the
ledger, the console and `pending` all agree. Delete the file to take the item up again.

Exit 0 ok; 1 refused or failed (the JSON says why); 2 usage or environment.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from store_path import project_dir, store_dir  # noqa: E402

PY = sys.executable
WAIT_POLL_S = 3
ALIVE_S = 30          # a director touches runs/<id>/director.alive every few seconds; older than this, it is gone


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def emit(doc: dict, code: int = 0) -> int:
    print(json.dumps(doc, ensure_ascii=False))
    return code


def who(by: str | None) -> str:
    if by and by.strip():
        return by.strip()
    if os.environ.get("DAPILOT_USER"):
        return os.environ["DAPILOT_USER"]
    try:
        name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=5).stdout.strip()
        if name:
            return name
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        return getpass.getuser()
    except Exception:  # no login name on this system
        return "session"


# --------------------------------------------------------------------------- the gate's view of a work dir

def offer_units(wd: Path) -> int:
    o = (read(wd / "viability.json") or {}).get("offer")
    u = o.get("units") if isinstance(o, dict) else None
    return u if isinstance(u, int) and not isinstance(u, bool) and u > 0 else 1


def lock_fresh(wd: Path) -> bool:
    try:
        import ledger  # noqa: WPS433  the same staleness rule the director uses
        return (wd / ".lock").exists() and not ledger.lock_is_stale(wd)
    except Exception:
        return (wd / ".lock").exists()


def item_view(wd: Path) -> dict:
    meta = read(wd / "meta.json") or {}
    proposal = read(wd / "price.json") or {}
    cands = read(wd / "price.candidates.json") or {}
    candidates = []
    for c in cands.get("candidates") or []:
        candidates.append({"label": c.get("label"), "price": c.get("price"), "margin_pct": c.get("margin_pct"),
                           "how": c.get("how"), "position_in_band": c.get("position_in_band"),
                           "proposed": proposal.get("candidate") == c.get("label")})
    # the proposal first, then the rest in the script's order
    candidates.sort(key=lambda c: 0 if c["proposed"] else 1)
    rejected = [{"label": c.get("label"), "price": c.get("price"), "margin_pct": c.get("margin_pct"), "because": c.get("rejected_because")}
                for c in cands.get("rejected") or []]
    return {"hash": wd.name, "workdir": str(wd), "file": meta.get("file") or wd.name,
            "currency": cands.get("currency") or proposal.get("currency") or "USD",
            "units": offer_units(wd), "floor": cands.get("floor", proposal.get("floor")),
            "verdict": proposal.get("verdict") or cands.get("verdict"),
            "market": {k: (cands.get("market") or {}).get(k) for k in ("n", "median", "p25", "p75")},
            "proposal": {k: proposal.get(k) for k in ("price", "candidate", "position", "rationale", "runner_up")},
            "candidates": candidates, "rejected_candidates": rejected}


def directors(root: Path) -> list[dict]:
    """Runs whose director is alive now: run_intake.py touches runs/<id>/director.alive while it works,
    whatever its workers are doing. The file is the liveness signal; a worker's .lock is not relied on."""
    runs = root / "runs"
    out = []
    if not runs.is_dir():
        return out
    now_t = time.time()
    for rd in runs.iterdir():
        alive = rd / "director.alive"
        try:
            age = now_t - alive.stat().st_mtime
        except OSError:
            continue
        if age > ALIVE_S:
            continue
        doc = read(alive) or {}
        state = read(rd / "state.json") or {}
        items = {}
        for h, it in (state.get("items") or {}).items():
            stage = next((st for st, rec in (it.get("stages") or {}).items() if rec.get("status") == "running"), None)
            items[h] = {"file": it.get("file"), "stage": stage}
        out.append({"run": rd.name, "pid": doc.get("pid"), "age_s": int(age), "items": items})
    return out


def scan(root: Path) -> dict:
    work = root / "work"
    pending, running, declined, rejected, before = [], [], [], [], []
    live = directors(root)
    held = {h: (d["run"], it) for d in live for h, it in d["items"].items()}
    if work.is_dir():
        for wd in sorted(work.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not (wd / "meta.json").exists():
                continue
            meta = read(wd / "meta.json") or {}
            name = meta.get("file") or wd.name
            if (wd / "price.approved.json").exists():
                continue
            if wd.name in held and not (wd / "price.json").exists():
                run, it = held[wd.name]
                stage = it.get("stage")
                if not stage and lock_fresh(wd):
                    try:
                        import ledger
                        stage = ledger.lock_stage(wd)
                    except Exception:
                        pass
                running.append({"hash": wd.name, "file": name, "stage": stage, "run": run})
                continue
            if lock_fresh(wd) and not (wd / "price.json").exists():
                try:
                    import ledger
                    stage = ledger.lock_stage(wd)
                except Exception:
                    stage = None
                running.append({"hash": wd.name, "file": name, "stage": stage, "run": None})
                continue
            if (wd / "design.blocked.json").exists():
                m = read(wd / "design.blocked.json") or {}
                declined.append({"hash": wd.name, "file": name, "reason": m.get("question") or m.get("reason")})
                continue
            if (wd / "price.rejected.json").exists():
                r = read(wd / "price.rejected.json") or {}
                rejected.append({"hash": wd.name, "file": name, "reason": r.get("reason") or r.get("verdict") or "not sellable"})
                continue
            if (wd / "price.json").exists():
                pending.append(item_view(wd))
                continue
            if not any((wd / f"{s}.failed.json").exists() or (wd / f"{s}.blocked.json").exists()
                       for s in ("analyze", "quote", "preview", "research", "price")) \
                    and not (wd / "quote.error.json").exists() and not (wd / "analysis.error.json").exists():
                before.append({"hash": wd.name, "file": name})
    # an item still before its price with no director alive is stopped, not running; it is listed so the
    # model can say so, and the person restarts it with quickstart
    before = [b for b in before if b["hash"] not in held]
    try:
        monitor_alive = time.time() - (root / "runs" / ".monitor.alive").stat().st_mtime < 10
    except OSError:
        monitor_alive = False
    return {"pending": pending, "running": running, "declined": declined, "rejected": rejected, "before_price": before,
            "directors": [{k: d[k] for k in ("run", "pid", "age_s")} for d in live],
            # the plugin's monitor (watch_run.py) is feeding this session: no need to poll, its lines arrive as they happen
            "monitor_alive": monitor_alive}


def cmd_pending(a) -> int:
    root = project_dir()
    deadline = time.time() + max(0, a.wait or 0)
    while True:
        s = scan(root)
        # idle: nothing to decide and no director alive. While a director lives, a wait goes on: a worker on
        # the slicer or on fal writes nothing for minutes, and that silence is not a stall.
        idle = not s["pending"] and not s["directors"] and not s["running"]
        if s["pending"] or not a.wait or time.time() >= deadline or idle:
            return emit({"ok": True, **s, "idle": idle, "waited_s": int(a.wait - max(0, deadline - time.time())) if a.wait else 0})
        time.sleep(WAIT_POLL_S)


# --------------------------------------------------------------------------- approve

def pricing_floor(wd: Path) -> tuple[object, float, float]:
    """(model, cost of one sale, floor), exactly as price_candidates.py builds them."""
    from pricing import Model, load_fees, load_policy, unit_cost_basis  # noqa: WPS433
    quote = read(wd / "quote.json")
    if not quote:
        raise ValueError("not quoted yet")
    policy, _ = load_policy()
    fees, _ = load_fees(policy)
    model = Model(policy, fees)
    cost = model.cost(unit_cost_basis(quote, policy)["unit_price"]) * offer_units(wd)
    return model, cost, model.round_price(model.break_even(cost), "up")


def start_director(root: Path, store: Path, item: str) -> str:
    run = "approved-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + item[:6]
    log_dir = root / "runs" / run
    log_dir.mkdir(parents=True, exist_ok=True)
    py = os.environ.get("DAPILOT_PYTHON") or PY
    with (log_dir / "director.log").open("a") as log:
        kw = {"start_new_session": True} if os.name != "nt" else {"creationflags": 0x00000008 | 0x00000200}  # DETACHED | NEW_PROCESS_GROUP
        subprocess.Popen([py, str(HERE / "run_intake.py"), "--run", run, "--items", item], cwd=root,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         env={**os.environ, "DAPILOT_STORE_DIR": str(store), "DAPILOT_PROJECT_DIR": str(root)}, **kw)
    return run


def cmd_approve(a) -> int:
    root, store = project_dir(), store_dir()
    os.environ["DAPILOT_STORE_DIR"], os.environ["DAPILOT_PROJECT_DIR"] = str(store), str(root)
    wd = Path(a.workdir).expanduser().resolve()
    if not (wd / "meta.json").exists():
        return emit({"ok": False, "error": f"no item at {wd}"}, 2)
    if lock_fresh(wd):
        return emit({"ok": False, "error": "a worker holds this item; try again when its stage ends"}, 1)
    if (wd / "price.approved.json").exists():
        prev = read(wd / "price.approved.json") or {}
        return emit({"ok": True, "cached": True, "price": prev.get("price"), "currency": prev.get("currency"), "by": prev.get("by"), "at": prev.get("at")})
    proposal = read(wd / "price.json")
    source = read(wd / "price.candidates.json") or read(wd / "price.rejected.json")
    if not source:
        return emit({"ok": False, "error": "the price stage has not run yet"}, 1)
    by = who(a.by)
    price = a.price if a.price is not None else (proposal or {}).get("price")
    if not isinstance(price, (int, float)) or price <= 0:
        return emit({"ok": False, "error": "no price given and no proposal to approve"}, 1)
    price = round(float(price), 2)
    currency = source.get("currency") or (proposal or {}).get("currency") or "USD"

    same = proposal is not None and abs(proposal.get("price", 0) - price) < 0.005 and not proposal.get("override")
    first = (proposal or {}).get("proposed")
    if not same and first and abs(first.get("price", 0) - price) < 0.005:
        (wd / "price.json").write_text(json.dumps(first, indent=2))   # back to the model's own proposal
        proposal, same = first, True
    if not same:
        label = next((c["label"] for c in source.get("candidates", []) if abs(c["price"] - price) < 0.005), None)
        reason = (a.reason or "").strip() or (f"{by} chose the {label} candidate over the proposal." if label else f"{by} set this price at the gate.")
        first = (proposal or {}).get("proposed") or ({k: v for k, v in proposal.items() if k != "override"} if proposal else None)
        chosen = {"price": price, "currency": currency, "candidate": label or "override",
                  "position": (proposal or {}).get("position") if label and proposal and proposal.get("candidate") == label else None,
                  "rationale": reason, "runner_up": None, "floor": source.get("floor"), "verdict": source.get("verdict"),
                  "offer": {"units": offer_units(wd)},
                  "override": {"by": by, "reason": reason, "at": now()}, "proposed": first}
        pending = wd / "price.pending.json"
        pending.write_text(json.dumps(chosen, indent=2))
        r = subprocess.run([PY, str(HERE / "price_candidates.py"), "--workdir", str(wd), "--check", str(pending)],
                           capture_output=True, text=True, cwd=root)
        try:
            verdict = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            verdict = {"errors": [r.stderr.strip()[-300:] or "the check produced no result"]}
        if r.returncode != 0:
            pending.unlink(missing_ok=True)
            return emit({"ok": False, "refused": True, "floor": source.get("floor"), "currency": currency,
                         "error": "; ".join(verdict.get("errors") or ["the check refused this price"])}, 1)
        pending.replace(wd / "price.json")
    try:
        model, cost, floor = pricing_floor(wd)
    except Exception as e:
        return emit({"ok": False, "error": f"pricing policy: {e}"}, 1)
    if price + 1e-9 < floor:
        return emit({"ok": False, "refused": True, "floor": floor, "currency": currency,
                     "error": f"{price:.2f} is below the floor {floor:.2f} {currency}"}, 1)
    units = offer_units(wd)
    point = model.point("approved", price, cost)
    (wd / "price.approved.json").write_text(json.dumps(
        {"price": price, "currency": currency, "by": by, "at": now(), "source": "proposal" if same else "override",
         "floor": floor, "unit_cost": point["unit_cost"], "margin": point["margin"], "margin_pct": point["margin_pct"],
         "offer": {"units": units, "per_unit": round(price / units, 2)}}, indent=2))
    rec = subprocess.run([PY, str(HERE / "product_record.py"), "--workdir", str(wd), "--verify-key"],
                         capture_output=True, text=True, cwd=root)
    filed = rec.returncode == 0
    run = None if a.no_start else start_director(root, store, wd.name)
    return emit({"ok": True, "price": price, "currency": currency, "by": by, "source": "proposal" if same else "override",
                 "floor": floor, "margin_pct": point["margin_pct"], "units": units, "product_filed": filed,
                 **({} if filed else {"product_note": (rec.stderr.strip() or rec.stdout.strip())[-200:]}),
                 "run": run, "file": (read(wd / "meta.json") or {}).get("file")})


def cmd_decline(a) -> int:
    root = project_dir()
    wd = Path(a.workdir).expanduser().resolve()
    if not (wd / "meta.json").exists():
        return emit({"ok": False, "error": f"no item at {wd}"}, 2)
    if (wd / "price.approved.json").exists():
        return emit({"ok": False, "error": "already approved; delete price.approved.json first if that was a mistake"}, 1)
    by = who(a.by)
    text = f"price declined by {by}" + (f": {a.reason.strip()}" if a.reason and a.reason.strip() else "")
    try:
        import ledger
        ledger.write_marker(wd, "design", "blocked", "review", 0, text)
    except Exception:
        (wd / "design.blocked.json").write_text(json.dumps({"stage": "design", "status": "blocked", "run": "review", "attempt": 0,
                                                            "question": text, "at": now()}, indent=2))
    return emit({"ok": True, "declined": True, "by": by, "file": (read(wd / "meta.json") or {}).get("file"),
                 "clear": "delete design.blocked.json to bring the item back to the gate"})


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows pipes default to cp1252
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pending"); p.add_argument("--wait", type=int, default=0, help="poll up to this many seconds for something to decide")
    p.set_defaults(fn=cmd_pending)
    p = sub.add_parser("approve"); p.add_argument("--workdir", required=True); p.add_argument("--price", type=float)
    p.add_argument("--by"); p.add_argument("--reason"); p.add_argument("--no-start", action="store_true", help="write the approval, start no director")
    p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("decline"); p.add_argument("--workdir", required=True); p.add_argument("--by"); p.add_argument("--reason")
    p.set_defaults(fn=cmd_decline)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
