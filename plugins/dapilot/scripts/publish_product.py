#!/usr/bin/env python3
"""The publish stage: one approved, designed, written product goes out. No model, no judgement.

    publish_product.py --workdir work/<hash> [--dry-run]

In order, each step idempotent on its own files, so a rerun continues where the last one stopped:

    1. etsy_listing.py draft      the listing as a draft (publish.json)
    2. etsy_listing.py media      photos in listing order, then the video
    3. etsy_listing.py activate   live on etsy.com; Etsy charges its listing fee      [publish.auto_activate]
    4. the product record's storefront block
    5. social_post.py post        every post in social/posts.json, on every enabled platform   [publish.auto_post]

and then published.json, whose existence completes the stage.

The gate is upstream. A person approved the sale price (price.approved.json); since 2026-09-21
that approval stands for everything that follows from it, which is why this script passes the
approver's name to `activate` and `--confirm` to `post`. Either half can be switched back to a
person in store/config.json: "publish": {"auto_activate": false, "auto_post": false}. With a
switch off the step is skipped, named in published.json, and the console still offers it.

An approval only stands for what it meant when it was given. `publish.auto_since` is the moment
automatic publishing was switched on; a price approved before it gets its draft and its media and
then stops, failed with the reason, until a person approves the price again.

Exit 0 done, 1 failed, 2 missing inputs, 3 a platform has not shown a post yet (run again).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable


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


def run(script: str, *args: str, timeout: int = 900) -> tuple[int, dict]:
    p = subprocess.run([PY, str(HERE / script), *args], capture_output=True, text=True, timeout=timeout)
    text = p.stdout.strip() or p.stderr.strip()
    try:
        return p.returncode, json.loads(text)
    except ValueError:
        line = next((l for l in reversed(text.splitlines()) if l.startswith("{")), "")
        try:
            return p.returncode, json.loads(line)
        except ValueError:
            return p.returncode or 1, {"ok": False, "error": text[-300:]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--dry-run", action="store_true", help="say what would happen; send nothing")
    a = ap.parse_args()
    wd = Path(a.workdir)
    store = os.environ.get("DAPILOT_STORE_DIR")
    if not store:
        return emit({"ok": False, "reason": "DAPILOT_STORE_DIR is not set"}, 2)
    missing = [n for n in ("listing.json", "design.json", "price.approved.json") if not (wd / n).exists()]
    if missing:
        return emit({"ok": False, "reason": f"missing inputs: {missing}"}, 2)
    if (wd / "published.json").exists():
        return emit({"ok": True, "cached": True, "published": "published.json"})

    store_cfg = read(Path(store) / "config.json") or {}
    cfg = store_cfg.get("publish") or {}
    auto_activate, auto_post = cfg.get("auto_activate", True), cfg.get("auto_post", True)
    # A platform switched off in config (Instagram while its browser adapter is not built, 2026-09-23) is
    # skipped by name, not failed: the post stays a draft in social/posts.json for the Social tab.
    platforms_on = {k for k, v in ((store_cfg.get("social") or {}).get("platforms") or {}).items() if v.get("enabled", True)}
    approved = read(wd / "price.approved.json") or {}
    by = approved.get("by") or "console"
    since = cfg.get("auto_since")
    stale = bool(since and (approved.get("at") or "") < since)
    posts = (read(wd / "social" / "posts.json") or {}).get("posts") or []

    if a.dry_run:
        pub = read(wd / "publish.json") or {}
        return emit({"ok": True, "dry_run": True, "would": [
            "use the existing draft" if pub.get("listing_id") else "create the Etsy draft",
            "upload photos and video not yet recorded in publish.json",
            *([f"stop there: the price was approved {approved.get('at')}, before automatic publishing began ({since}); "
               "approve it again to publish"] if stale else [
                (f"activate the listing at {approved.get('price')} {approved.get('currency')}, approved by {by}"
                 if auto_activate else "leave the listing a draft (publish.auto_activate is off)"),
                *([f"post {p['platform']}.{p['format']}" if p['platform'] in platforms_on else f"skip {p['platform']}.{p['format']} (platform disabled in config)"
                   for p in posts] if auto_post else ["post nothing (publish.auto_post is off)"])]),
        ]})

    steps: list[dict] = []

    def step(name: str, code: int, res: dict) -> bool:
        ok = code == 0 and res.get("ok", True) is not False
        steps.append({"step": name, "ok": ok, **{k: res[k] for k in ("listing_id", "state", "url", "post_id", "cached", "error", "reason") if k in res}})
        return ok

    env = ["--env", str(Path(store) / ".env")] if (Path(store) / ".env").exists() else []
    code, res = run("etsy_listing.py", *env, "draft", "--workdir", str(wd))
    if not step("etsy draft", code, res):
        return emit({"ok": False, "reason": f"etsy draft: {res.get('error')}", "steps": steps}, 1)
    code, res = run("etsy_listing.py", *env, "media", "--workdir", str(wd))
    if not step("etsy media", code, res):
        return emit({"ok": False, "reason": f"etsy media: {res.get('error')}", "steps": steps}, 1)

    if stale:
        return emit({"ok": False, "reason": f"the draft and its media are on Etsy; the price was approved {approved.get('at')}, before "
                                            f"automatic publishing began ({since}), so nothing went public. Approve the price again to publish",
                     "steps": steps}, 1)
    skipped = []
    if auto_activate:
        if (read(wd / "publish.json") or {}).get("state") != "active":
            code, res = run("etsy_listing.py", *env, "activate", "--workdir", str(wd), "--by", by,
                            "--reason", f"sale price approved {approved.get('at')}; published by the pipeline")
            if not step("etsy activate", code, res):
                return emit({"ok": False, "reason": f"etsy activate: {res.get('error')}", "steps": steps}, 1)
    else:
        skipped.append("etsy activate")

    pub = read(wd / "publish.json") or {}
    rec_p = Path(store) / "products" / f"{(read(wd / 'meta.json') or {}).get('sha256') or wd.name}.json"
    rec = read(rec_p)
    if rec:
        rec["storefront"] = {"platform": "etsy", "state": pub.get("state"), "listing_id": pub.get("listing_id"), "url": pub.get("url")}
        rec["updated_at"] = now()
        rec_p.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")

    pending = False
    if auto_post and pub.get("state") == "active":
        for p in posts:
            if p["platform"] not in platforms_on:
                skipped.append(f"{p['platform']}.{p['format']} (platform disabled in config)")
                continue
            code, res = run("social_post.py", "post", "--workdir", str(wd), "--platform", p["platform"],
                            "--format", p["format"], "--confirm")
            if code == 3:
                pending = True
                steps.append({"step": f"{p['platform']}.{p['format']}", "ok": False, "pending": True})
            elif not step(f"{p['platform']}.{p['format']}", code, res):
                # One platform failing does not unpublish the rest; it is named and the stage still ends.
                continue
    elif posts:
        skipped.append("social posts" + ("" if auto_post else " (publish.auto_post is off)") +
                       ("" if pub.get("state") == "active" else " (the listing is not live, so the link would lead nowhere)"))
    if pending:
        return emit({"ok": False, "pending": True, "reason": "a platform has not shown a post yet; run again, it only confirms", "steps": steps}, 3)

    done = {"ok": True, "at": now(), "approved_by": by,
            "etsy": {"listing_id": pub.get("listing_id"), "state": pub.get("state"), "url": pub.get("url")},
            "social": [s for s in steps if "." in s["step"]], "skipped": skipped, "steps": steps}
    (wd / "published.json").write_text(json.dumps(done, indent=1, ensure_ascii=False) + "\n")
    failed = [s["step"] for s in steps if not s["ok"]]
    return emit({"ok": True, "published": "published.json", "etsy": done["etsy"], "posted": [s["step"] for s in done["social"] if s["ok"]],
                 "failed": failed, "skipped": skipped})


if __name__ == "__main__":
    sys.exit(main())
