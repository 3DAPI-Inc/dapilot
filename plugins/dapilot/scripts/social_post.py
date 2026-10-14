#!/usr/bin/env python3
"""Post a listed product to a social platform, once, through that platform's integration.

    social_post.py session status|keepalive|login [--platform P] [--account A] [--reconnect] [--redirected-url URL]
    social_post.py check   --workdir work/<hash>
    social_post.py post    --workdir work/<hash> --platform P [--format F] [--confirm]
    social_post.py metrics --workdir work/<hash> [--platform P]

Deterministic. The words are a judgement and arrive in social/posts.json; the media is
what the listing already carries, named in publish.json (Etsy's public URLs, which is what
an API platform fetches) and design.json (the local files, which is what a browser uploads).
Platforms, accounts and adapters are the `social` block of store/config.json:

    "social": {"platforms": {"instagram": {"adapter": "instagram_api", "account": "shop", "enabled": true}}}

An adapter is a module with build(account, store_dir, config) returning an Integration
(social/base.py). Modules in social/ ship with the plugin; any other name is looked up in
DAPILOT_SOCIAL_ADAPTER_DIR, which is where the browser-driven platforms live.

social/posts.json, one entry per platform and format:
    {"posts": [{"platform": "instagram", "format": "reel", "caption": "...", "hashtags": ["..."],
                "media": ["video"]},
               {"platform": "instagram", "format": "carousel", "caption": "...", "hashtags": [],
                "media": ["photo:1", "photo:2", "photo:3"], "alt_text": {"photo:1": "..."}}]}
`video` is the listing video, `photo:N` the listing photo of rank N.

State per post under social/: <platform>.<format>.pending.json is written before anything is
sent and carries the adapter's checkpoint; <platform>.<format>.json is written only after the
platform shows the post, with its id and URL. A rerun that finds a pending file confirms or
resumes and never starts over. Posting is public, so `post` without --confirm prints what it
would send and stops.

`session keepalive` is the daily job: every enabled platform's session is looked at, renewed
when due, and reported. Exit codes: 0 ok, 1 failed, 2 usage or environment, 3 not visible
yet or still processing (run again), 4 a person has to log in or clear a challenge.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from social.base import Media, Post, SocialError, to_json  # noqa: E402


def emit(doc: dict, code: int = 0) -> int:
    print(json.dumps(doc, indent=1, ensure_ascii=False))
    return code


def read(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(path)


def store_dir() -> Path:
    d = os.environ.get("DAPILOT_STORE_DIR")
    if not d:
        raise SocialError("DAPILOT_STORE_DIR is not set", 2)
    return Path(d)


def platforms(only: str | None = None) -> dict:
    cfg = (read(store_dir() / "config.json") or {}).get("social") or {}
    found = {k: v for k, v in (cfg.get("platforms") or {}).items() if v.get("enabled", True)}
    if only:
        if only not in found:
            raise SocialError(f"{only} is not an enabled platform in the social block of store/config.json", 2)
        found = {only: found[only]}
    if not found:
        raise SocialError("store/config.json has no enabled social platform", 2)
    return found


def integration(platform: str, cfg: dict, account: str | None = None):
    name = cfg.get("adapter")
    if not name:
        raise SocialError(f"no adapter configured for {platform}", 2)
    try:
        module = importlib.import_module(f"social.{name}")
    except ModuleNotFoundError:
        extra = os.environ.get("DAPILOT_SOCIAL_ADAPTER_DIR")
        if not extra or not (Path(extra) / f"{name}.py").is_file():
            raise SocialError(f"adapter {name} for {platform} is not in the plugin; set DAPILOT_SOCIAL_ADAPTER_DIR "
                              "to the directory that holds it", 2)
        sys.path.insert(0, extra)
        module = importlib.import_module(name)
    return module.build(account or cfg.get("account") or "default", store_dir(), cfg)


def load_posts(wd: Path) -> list[Post]:
    doc = read(wd / "social" / "posts.json")
    if not isinstance(doc, dict) or not isinstance(doc.get("posts"), list):
        raise SocialError("social/posts.json is missing: the captions are written before anything is posted", 2)
    pub, design = read(wd / "publish.json") or {}, read(wd / "design.json") or {}
    by_rank = {i.get("rank"): i for i in pub.get("images") or []}
    local = {n + 1: p.get("file") for n, p in enumerate(design.get("photos") or [])}
    meta = read(wd / "meta.json") or {}
    posts = []
    for row in doc["posts"]:
        media = []
        for ref in row.get("media") or []:
            alt = (row.get("alt_text") or {}).get(ref)
            if ref == "video":
                v = pub.get("video") or {}
                f = (design.get("video") or {}).get("file")
                media.append(Media("video", v.get("url"), str(wd / f) if f else None, alt))
            elif ref.startswith("photo:") and ref[6:].isdigit():
                n = int(ref[6:])
                f = local.get(n)
                media.append(Media("image", (by_rank.get(n) or {}).get("url"), str(wd / f) if f else None, alt))
            else:
                raise SocialError(f"posts.json: media {ref!r} is neither 'video' nor 'photo:N'", 2)
        posts.append(Post(item=meta.get("sha256") or wd.name, platform=row.get("platform"), format=row.get("format"),
                          caption=(row.get("caption") or "").strip(), hashtags=list(row.get("hashtags") or []),
                          media=media, link=pub.get("url")))
    return posts


# ---------------------------------------------------------------- commands

def cmd_session(a) -> int:
    rows, worst = [], 0
    for platform, cfg in platforms(a.platform).items():
        session = integration(platform, cfg, a.account).session
        if a.action == "login":
            if not hasattr(session, "login"):
                raise SocialError(f"{platform}: {session.login_help()}", 4)
            st = session.login(a.redirected_url) if a.redirected_url else session.login()
        else:
            st = session.keepalive() if a.action == "keepalive" else session.status()
            if a.reconnect and not st.usable and hasattr(session, "login"):
                st = session.login()  # a person still has to allow it; this only opens the page for them
        rows.append(to_json(st))
        if not st.usable:
            worst = 4
    return emit({"ok": worst == 0, "sessions": rows}, worst)


def cmd_check(a) -> int:
    wd = Path(a.workdir)
    enabled = platforms()
    report, bad = [], False
    for post in load_posts(wd):
        if post.platform not in enabled:
            report.append({"post": f"{post.platform}.{post.format}", "errors": ["platform is not enabled in config"]})
            bad = True
            continue
        errors = integration(post.platform, enabled[post.platform]).check(post)
        if not post.caption:
            errors.append("caption is empty")
        bad = bad or bool(errors)
        report.append({"post": f"{post.platform}.{post.format}", "errors": errors})
    return emit({"ok": not bad, "posts": report}, 1 if bad else 0)


def cmd_post(a) -> int:
    wd = Path(a.workdir)
    cfg = platforms(a.platform)[a.platform]
    mine = [p for p in load_posts(wd) if p.platform == a.platform and (not a.format or p.format == a.format)]
    if len(mine) != 1:
        raise SocialError(f"posts.json has {len(mine)} posts for {a.platform}; name one with --format", 2)
    post = mine[0]
    tool = integration(a.platform, cfg)
    errors = tool.check(post)
    if errors:
        return emit({"ok": False, "errors": errors}, 1)

    # Named in full: with_suffix() would take ".reel" for the suffix and file every format under one name.
    stem = f"{post.platform}.{post.format}"
    done_p, pending_p = wd / "social" / f"{stem}.json", wd / "social" / f"{stem}.pending.json"
    done = read(done_p)
    if done:
        return emit({"ok": True, "cached": True, "post_id": done.get("post_id"), "url": done.get("url")})
    if not a.confirm:
        return emit({"ok": True, "sent": False, "note": "nothing sent; a person passes --confirm",
                     "would_post": {"platform": post.platform, "format": post.format, "text": post.text(),
                                    "media": [m.url or m.file for m in post.media]}})
    status = tool.session.keepalive()
    if not status.usable:
        return emit({"ok": False, "reason": f"{a.platform} session is {status.state}", "help": tool.session.login_help()}, 4)

    pending = read(pending_p)
    if pending and pending.get("fingerprint") != post.fingerprint():
        raise SocialError("an earlier attempt is pending for a different caption or media; a person looks at "
                          f"{pending_p.name} and the account before anything is sent", 1)
    published = tool.confirm(post, pending["checkpoint"]) if pending and pending.get("checkpoint") else None
    if published is None:
        if not pending:
            pending = {"fingerprint": post.fingerprint(), "started_at": time.time(), "checkpoint": None}
            write(pending_p, pending)

        def save(checkpoint: dict) -> None:
            pending["checkpoint"] = checkpoint
            write(pending_p, pending)

        tool.submit(post, save, resume=pending.get("checkpoint"))
        published = tool.confirm(post, pending["checkpoint"])
    if published is None:
        return emit({"ok": False, "pending": True, "reason": "submitted, not visible yet; run post again, it only confirms"}, 3)
    write(done_p, {"ok": True, "platform": post.platform, "format": post.format, "account": tool.session.account,
                   "fingerprint": post.fingerprint(), **to_json(published)})
    pending_p.rename(wd / "social" / f"{stem}.sent.json")
    return emit({"ok": True, "post_id": published.post_id, "url": published.url})


def cmd_metrics(a) -> int:
    wd = Path(a.workdir)
    rows = []
    for done_p in sorted((wd / "social").glob("*.json")):
        done = read(done_p) or {}
        if not done.get("post_id") or (a.platform and done.get("platform") != a.platform):
            continue
        tool = integration(done["platform"], platforms(done["platform"])[done["platform"]])
        row = {"post": done_p.stem, "at": time.time(), "age_h": round((time.time() - done.get("posted_at", 0)) / 3600, 1),
               **tool.metrics(done)}
        with (wd / "social" / "metrics.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
        rows.append(row)
    return emit({"ok": True, "metrics": rows})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("session")
    s.add_argument("action", choices=["status", "keepalive", "login"])
    s.add_argument("--platform")
    s.add_argument("--account")
    s.add_argument("--reconnect", action="store_true", help="keepalive: open the sign-in page when a session can no longer be renewed")
    s.add_argument("--redirected-url", help="login: the address the browser was sent back to, when the listener could not catch it")
    c = sub.add_parser("check")
    c.add_argument("--workdir", required=True)
    p = sub.add_parser("post")
    p.add_argument("--workdir", required=True)
    p.add_argument("--platform", required=True)
    p.add_argument("--format")
    p.add_argument("--confirm", action="store_true", help="send it; without this the post is printed and nothing is sent")
    m = sub.add_parser("metrics")
    m.add_argument("--workdir", required=True)
    m.add_argument("--platform")
    a = ap.parse_args()
    try:
        return {"session": cmd_session, "check": cmd_check, "post": cmd_post, "metrics": cmd_metrics}[a.cmd](a)
    except SocialError as e:
        return emit({"ok": False, "reason": e.reason, **e.extra}, e.code)


if __name__ == "__main__":
    sys.exit(main())
