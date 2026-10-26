#!/usr/bin/env python3
"""The supervisor: one decision per stopped stage, run inside the runner's container.

  supervisor.py serve                                        poll the supervise queue forever, as its own process
  supervisor.py once --message FILE [--local-backend DIR]    handle one message from a JSON file, for a laptop test

Where it runs: the runner's consumer starts this module's loop in a second thread of its own process when
DAPILOT_SUPERVISE_QUEUE_URL is set (consumer.serve), so the one Coolify resource does both and the only
setup is that variable. `serve` here is the standalone form, for a laptop or for splitting the two apart
later; `doctor.sh --supervise` runs it.

What wakes it (docs/hosting.md, "Supervisor"): one message per stage that stopped failed, sent by the
3dapi backend after it has stored the outcome, so the row always holds the failure before anyone acts:

  {"kind": "supervise", "seller": "<id>", "run": "<id>", "item": "<hash>", "stage": "<stage>",
   "marker": "failed", "reason": "<text>", "error": "<code, optional>", "previous": "<text, optional>",
   "retries": 0, "fingerprint": "<hex>", "changed": false, "trigger": "outcome|resume|startup|config"}

A `marker` of `review` (2026-10-23) is a done stage whose output a check flagged (the ledger's
`<stage>.review.json`; `reason` is the problems joined). Triage's Review kind redoes it once for a
plain defect through the same retry route, which the ledger honours only while nothing later has
used the outputs, and otherwise leaves a `supervisor.left` note with the problems for the seller.

What it does with one. It looks at the run first (GET /internal/dapilot/runs/<id>): a cancelled run and
a stage that is no longer stopped are left alone whatever the message says, so a stale or repeated
message costs one GET. Then triage.py decides, one class per kind of stop, the only question being
whether the next attempt could end differently:

  retry now      POST /internal/dapilot/runs/<id>/retry: the same message and event a seller's Try again
                 sends, with the kind and the sentence as the ledger's reason and `by: supervisor`
  retry later    the same body goes back onto this queue with `act: "retry"` and `not_before`, delayed
                 as far as SQS allows (900 s) and re-queued until the time comes; when it does, the run
                 is looked at again before the retry is sent
  leave          one event on the run, `supervisor.left`, with the code, the sentence and what the stop
                 waits for; the dashboard shows it under the failure

A third answer since 2026-10-26, `redo`: the stopped stage is fine and an earlier stage's output is the
problem (an empty analysis, drafts of the wrong thing); the retry route is sent with `redo` and
`cascade` for that earlier stage, and the ledger sets its outputs and everything after aside and runs
from there, refusing past a stage that cost fal assets. There is no escalate, because a stop it cannot
make work is already on the dashboard exactly as it is. It never touches the bucket or a store, and it
never sends to the runs queue itself; everything it changes is a call to the backend, which records it
like a person's.

Who decides (2026-10-26): a model. The question is interpretation, not arithmetic, and the rules in
triage.py were a dictionary of the failures already seen, growing one pattern per surprise. Now the
runner gives the stop, the item's record from the outcome (every stage, the worker's account, the
report, the other markers, the spend) and the run's status to `claude -p --agent dapilot:supervisor`,
the plugin's supervise-run skill, with no tools and a two-turn, forty-cent cap (a call costs about ten cents), and reads one JSON
line back. The runner keeps the hard limits as code: no more than MAX_RETRIES retries per stage, a
redo only of an earlier stage, never a blocked marker, and the ledger's refusal to set a paid stage
aside. When the model cannot be asked (no CLI, no key, a malformed answer) triage.py's rules decide as
before, and the event says `decided_by`.

Environment: DAPILOT_SUPERVISE_QUEUE_URL, DAPILOT_BACKEND_URL (ends in /api), DAPILOT_BACKEND_TOKEN,
AWS_REGION, and the runner user's AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY. The laptop form takes a
directory for the backend: run.json is what GET answers, and retries, events and re-queued messages
are appended to retries.jsonl, events.jsonl and requeued.jsonl there.
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
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from consumer import PLUGIN_DIR, Backend, aws_region, log, poll  # noqa: E402  the loop and the backend client are the runner's
from triage import Decision, Stop, decide  # noqa: E402

MAX_DELAY_S = 900   # SQS's per-message ceiling; a longer wait is chained in hops of this
MAX_RETRIES = 2     # the supervisor's own cap per stage; past it the answer is leave, whatever the model says
STAGE_ORDER = ("analyze", "quote", "preview", "research", "price", "design", "copy", "publish")
MODEL_BUDGET_USD = "0.40"   # one call reads about 70k tokens of context (the plugin's skills and the CLI's own prompt) and costs about 0.10
MODEL_TIMEOUT_S = 150


def trimmed_item(item: dict | None) -> dict | None:
    """The item's record as the model reads it: every stage with its status, attempts, account and report, the
    markers and the spend; without the media keys and with the long lists cut. A few thousand tokens at most."""
    if not isinstance(item, dict):
        return None
    out: dict = {"stages": {}, "markers": item.get("markers") or [], "spend": item.get("spend")}
    for stage, rec in (item.get("stages") or {}).items():
        if not isinstance(rec, dict):
            continue
        r = {k: rec.get(k) for k in ("status", "attempt", "retries", "summary", "reason", "previous", "review", "spend", "account") if rec.get(k) is not None}
        if isinstance(rec.get("attempts"), list):
            r["attempts"] = [{k: a.get(k) for k in ("attempt", "status", "usd", "seconds", "interrupted") if k in a} for a in rec["attempts"][-6:] if isinstance(a, dict)]
        rep = rec.get("report")
        if isinstance(rep, dict):
            rep = {k: v for k, v in rep.items() if k not in ("description", "scenes", "clip_scene", "tags")}
            if isinstance(rep.get("similar"), list):
                rep["similar"] = rep["similar"][:3]
            r["report"] = rep
        if isinstance(r.get("account"), str):
            r["account"] = r["account"][:1500]
        out["stages"][stage] = r
    return out


def ask_model(stop: Stop, body: dict, look: dict, item: dict | None) -> tuple[Decision | None, dict]:
    """One `claude -p` call to the plugin's supervisor agent. Returns the decision and what the call cost, or
    None and the reason it could not be had, in which case the rules decide."""
    claude = shutil.which("claude")
    if not claude:
        return None, {"error": "claude CLI not on PATH"}
    prompt = {
        "stop": {"stage": stop.stage, "marker": stop.marker, "reason": stop.reason, "error": stop.error, "previous": stop.previous,
                 "retries": stop.retries, "retries_left": max(0, MAX_RETRIES - stop.retries), "changed": stop.changed,
                 "trigger": body.get("trigger"), "attempt": body.get("attempt")},
        "run": {"status": look.get("status"), "counts": (look.get("outcome") or {}).get("counts"),
                "other_stops": [s for s in look.get("stops", []) if not (s.get("item") == body["item"] and s.get("stage") == stop.stage)][:6]},
        "item": trimmed_item(item),
        "stage_order": list(STAGE_ORDER),
    }
    cmd = [claude, "-p", "--agent", "dapilot:supervisor", "--plugin-dir", str(PLUGIN_DIR), "--tools", "",
           "--max-turns", "2", "--max-budget-usd", MODEL_BUDGET_USD, "--output-format", "json", "--model", "sonnet",
           json.dumps(prompt, ensure_ascii=False)]
    env = {**os.environ, "DAPILOT_WORKER": "1"}
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=MODEL_TIMEOUT_S, env=env, cwd=str(PLUGIN_DIR))
    except subprocess.TimeoutExpired:
        return None, {"error": f"the model did not answer in {MODEL_TIMEOUT_S}s"}
    meta: dict = {"usd": 0.0}
    try:
        out = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
    except (ValueError, IndexError):
        out = {}
    meta["usd"] = round(float(out.get("total_cost_usd") or 0.0), 4)
    text = out.get("result") if isinstance(out.get("result"), str) else p.stdout
    answer = None
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip().strip("`")
        if line.startswith("{") and line.endswith("}"):
            try:
                answer = json.loads(line)
                break
            except ValueError:
                continue
    if p.returncode != 0 and answer is None:
        return None, {**meta, "error": f"claude -p exit {p.returncode}: {(p.stderr or p.stdout)[-300:]}"}
    if not isinstance(answer, dict):
        return None, {**meta, "error": f"no decision in the answer: {(text or '')[-300:]}"}
    # tolerate the near misses a model makes: `redo: true` on a retry, `reason` for `why`
    if answer.get("action") == "retry" and (answer.get("redo") is True) and answer.get("stage"):
        answer["action"] = "redo"
    if answer.get("action") not in ("retry", "redo", "leave"):
        return None, {**meta, "error": f"no decision in the answer: {(text or '')[-300:]}"}
    why = str(answer.get("why") or answer.get("reason") or "").strip()[:500] or "the supervisor gave no reason"
    confidence = str(answer.get("confidence") or "medium")
    code = f"model.{confidence}"
    if answer["action"] == "retry":
        try:
            delay = max(0, min(3600, int(float(answer.get("delay_s") or 0))))
        except (TypeError, ValueError):
            delay = 0
        return Decision("retry", code, why, delay_s=delay, note=f"supervisor: {why}", decided_by="model"), meta
    if answer["action"] == "redo":
        return Decision("redo", code, why, note=f"supervisor: {why}", redo_stage=str(answer.get("stage") or ""), decided_by="model"), meta
    return Decision("leave", code, why, wait_for=str(answer.get("wait_for") or "") or None, decided_by="model"), meta


def guard(d: Decision, stop: Stop, item: dict | None) -> Decision:
    """The limits the runner keeps as code, whatever the model said."""
    if d.action != "leave" and stop.retries >= MAX_RETRIES:
        return Decision("leave", d.code, f"{MAX_RETRIES} retries already; {d.why}", wait_for="a person", decided_by=d.decided_by)
    if d.action == "redo":
        stages = (item or {}).get("stages") or {}
        earlier = stop.stage in STAGE_ORDER and d.redo_stage in STAGE_ORDER and STAGE_ORDER.index(d.redo_stage) < STAGE_ORDER.index(stop.stage)
        if not earlier or (stages.get(d.redo_stage) or {}).get("status") not in ("done", None):
            return Decision("leave", d.code, f"a redo of {d.redo_stage or '?'} is not possible from {stop.stage}; {d.why}",
                            wait_for="a person", decided_by=d.decided_by)
    return d


class SupervisorBackend(Backend):
    """The runner's backend client plus the two calls only the supervisor makes."""

    def run(self, run: str) -> dict:
        """The run's status and its stopped stages, as the backend sees them now."""
        if self.local is not None:
            p = self.local / "run.json"
            return json.loads(p.read_text()) if p.exists() else {"status": "failed", "stops": []}
        return self._http("GET", f"/internal/dapilot/runs/{run}")

    def retry(self, seller: str, run: str, item: str, stage: str, reason: str, redo: bool = False, cascade: bool = False) -> dict:
        body = {"seller": seller, "item": item, "stage": stage, "reason": reason[:500], "by": "supervisor",
                **({"redo": True, "cascade": True} if redo else {})}
        if self.local is not None:
            with (self.local / "retries.jsonl").open("a") as f:
                f.write(json.dumps({"run": run, **body}) + "\n")
            return {"success": True}
        try:
            return self._http("POST", f"/internal/dapilot/runs/{run}/retry", body)
        except urllib.error.HTTPError as e:   # 409 is an answer (cancelled, moved on), not a crash
            return {"success": False, "status": e.code, "error": e.read().decode()[:300]}


class SelfQueue:
    """Where a delayed retry waits: this queue, with the delay SQS allows. A directory on a laptop."""

    def __init__(self, url: str | None, local: Path | None):
        self.url, self.local, self.sqs = url, local, None
        if local is None:
            import boto3
            self.sqs = boto3.client("sqs", region_name=aws_region())

    def later(self, body: dict, delay_s: float) -> int:
        delay = max(0, min(MAX_DELAY_S, int(delay_s)))
        if self.local is not None:
            with (self.local / "requeued.jsonl").open("a") as f:
                f.write(json.dumps({"delay_s": delay, **body}) + "\n")
            return delay
        self.sqs.send_message(QueueUrl=self.url, MessageBody=json.dumps(body), DelaySeconds=delay)
        return delay


def handle(body: dict, backend: SupervisorBackend, queue: SelfQueue) -> dict:
    seller, run, item, stage = body["seller"], body["run"], body["item"], body["stage"]
    tag = f"{run[:8]}/{item[:8]}/{stage}"

    look = backend.run(run)
    if look.get("status") == "cancelled":
        log(f"{tag}: the run is cancelled; nothing to do")
        return {"ok": True, "did": "nothing", "why": "cancelled"}
    current = next((s for s in look.get("stops", []) if s.get("item") == item and s.get("stage") == stage), None)
    if current is None or current.get("marker") not in ("failed", "review"):
        log(f"{tag}: the stage is no longer stopped or under review; nothing to do")
        return {"ok": True, "did": "nothing", "why": "moved on"}

    # A retry that waited its delay out, or has not yet.
    if body.get("act") == "retry":
        remaining = float(body.get("not_before") or 0) - time.time()
        if remaining > 1:
            hop = queue.later(body, remaining)
            log(f"{tag}: {int(remaining)}s left to wait; back on the queue for {hop}s")
            return {"ok": True, "did": "waited", "remaining_s": int(remaining)}
        return send_retry(backend, body, tag, body.get("note") or "scheduled retry", body.get("why"))

    stop = Stop(
        reason=current.get("reason") or body.get("reason") or "",
        error=current.get("error") or body.get("error"),
        marker=current.get("marker") or "failed",
        retries=int(current.get("retries") or body.get("retries") or 0),
        previous=current.get("previous") or body.get("previous"),
        changed=bool(body.get("changed")),
        stage=stage,
    )
    item_rec = next((it for it in ((look.get("outcome") or {}).get("items") or []) if it.get("item") == item), None)
    d, meta = ask_model(stop, body, look, item_rec)
    if d is None:
        log(f"{tag}: the model could not decide ({meta.get('error')}); the rules do")
        d = decide(stop)
    d = guard(d, stop, item_rec)
    common = {"item": item, "stage": stage, "code": d.code, "why": d.why, "retries": stop.retries,
              "trigger": body.get("trigger"), "decided_by": d.decided_by, "usd": meta.get("usd", 0.0)}
    if d.action == "redo":
        backend.event(seller, run, "supervisor.redo", redo_stage=d.redo_stage, **common)
        answer = backend.retry(seller, run, item, d.redo_stage or stage, d.note, redo=True, cascade=True)
        if answer.get("success"):
            log(f"{tag}: redo of {d.redo_stage} sent ({d.code}): {d.why}")
            return {"ok": True, "did": "redo", "stage": d.redo_stage, "code": d.code, "why": d.why}
        log(f"{tag}: redo refused by the backend: {answer.get('error') or answer.get('status')}")
        backend.event(seller, run, "supervisor.left", wait_for="a person", **{**common, "why": f"redo of {d.redo_stage} refused: {answer.get('error') or answer.get('status')}"})
        return {"ok": True, "did": "refused", "answer": answer}
    if d.action == "leave":
        backend.event(seller, run, "supervisor.left", wait_for=d.wait_for, **common)
        log(f"{tag}: left ({d.code}): {d.why}")
        return {"ok": True, "did": "left", "code": d.code, "why": d.why, "wait_for": d.wait_for}
    if d.delay_s > 0:
        backend.event(seller, run, "supervisor.scheduled", delay_s=d.delay_s, **common)
        later = {**body, "act": "retry", "note": d.note, "why": d.why, "code": d.code, "not_before": time.time() + d.delay_s}
        hop = queue.later(later, d.delay_s)
        log(f"{tag}: retry in {d.delay_s}s ({d.code}); first hop {hop}s")
        return {"ok": True, "did": "scheduled", "code": d.code, "delay_s": d.delay_s}
    return send_retry(backend, {**body, "code": d.code}, tag, d.note, d.why)


def send_retry(backend: SupervisorBackend, body: dict, tag: str, note: str, why: str | None) -> dict:
    seller, run, item, stage = body["seller"], body["run"], body["item"], body["stage"]
    backend.event(seller, run, "supervisor.retry", item=item, stage=stage, code=body.get("code"), why=why or note)
    answer = backend.retry(seller, run, item, stage, note)
    if answer.get("success"):
        log(f"{tag}: retry sent ({body.get('code')}): {note}")
        return {"ok": True, "did": "retried", "code": body.get("code")}
    log(f"{tag}: retry refused by the backend: {answer.get('error') or answer.get('status')}")
    return {"ok": True, "did": "refused", "answer": answer}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="poll the supervise queue forever")
    one = sub.add_parser("once", help="handle one message from a file")
    one.add_argument("--message", required=True)
    one.add_argument("--local-backend", help="a directory standing in for the backend (and for this queue)")
    a = ap.parse_args()
    local = Path(a.local_backend).resolve() if getattr(a, "local_backend", None) else None
    backend = SupervisorBackend(os.environ.get("DAPILOT_BACKEND_URL"), os.environ.get("DAPILOT_BACKEND_TOKEN"), local)
    queue = SelfQueue(os.environ.get("DAPILOT_SUPERVISE_QUEUE_URL"), local)
    if a.cmd == "serve":
        url = os.environ.get("DAPILOT_SUPERVISE_QUEUE_URL")
        if not url:
            print(json.dumps({"ok": False, "reason": "DAPILOT_SUPERVISE_QUEUE_URL is not set"})); return 2
        return poll(url, lambda body, _stop: handle(body, backend, queue), label="decision")
    body = json.loads(Path(a.message).read_text())
    result = handle(body, backend, queue)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
