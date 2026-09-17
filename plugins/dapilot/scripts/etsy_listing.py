#!/usr/bin/env python3
"""Etsy listing lifecycle for one work dir: draft, media, status, activate.

Deterministic. The copy comes from <workdir>/listing.json (written by the
copy stage), the price from price.json (never from listing.json, so the copy
cannot set it), the photos and video from design.json, the parcel weight
from quote.json metrics and the size from analysis.json. Every Etsy id the
script creates is recorded in <workdir>/publish.json, and every command is
idempotent on that file: a rerun never creates a second draft and never
uploads a photo whose rank is already recorded.

    etsy_listing.py shop                                   who the token is; shop id, name, currency
    etsy_listing.py profiles                               shipping profiles, return policies, readiness states
    etsy_listing.py draft    --workdir W                   createDraftListing -> publish.json {listing_id, state: draft}
    etsy_listing.py media    --workdir W                   upload photos in design.json order, then the video
    etsy_listing.py status   --workdir W                   the listing as Etsy sees it now
    etsy_listing.py activate --workdir W --by NAME --reason TEXT
                                                           the gate: publish on etsy.com; charges the listing fee

Environment: ETSY_KEYSTRING and ETSY_SHARED_SECRET (or --env store/.env),
DAPILOT_STORE_DIR for the token file written by etsy_auth.py login. Writes
need a token whose scope includes listings_w; `shop` and `profiles` need
shops_r. Scopes are granted at login, not per key:
    etsy_auth.py --env store/.env login --scopes "listings_r listings_w shops_r"

listing.json, the copy stage's output, the fields this script reads:
    title            <= 140 characters, Etsy's title rules
    description      plain text
    tags             up to 13, <= 20 characters each
    materials        strings of letters, digits and spaces
    taxonomy_id      a SELLER taxonomy id (seller-taxonomy/nodes), not the buyer id research uses
    who_made         i_did | someone_else | collective
    when_made        made_to_order | 2020_2026 | ...
    quantity         units offered; the stock run, or 1 for print to order
    type             physical (default)
    is_supply        false (default)
    should_auto_renew   false (default): a draft is not renewed for 0.20 USD every four months without a person
    shipping_profile_id, return_policy_id, readiness_state_id   optional on a draft, needed to activate a physical listing
    alt_text         {photo id: text}, optional; defaults to the title
    processing_min, processing_max   optional, days

Draft creation is free and private. Activation is the paid, public step and
this script refuses it without --by and --reason, which it records in
publish.json as the override block the guardrails expect.

Exit 0 on success, 1 on error, 2 when a prerequisite file is missing, 3 when
the token lacks the scope the command needs.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import etsy_auth  # noqa: E402

API = "https://openapi.etsy.com/v3/application"
TITLE_MAX = 140
TAGS_MAX = 13
TAG_LEN_MAX = 20
IMAGES_MAX = 20
ALT_MAX = 500


def die(msg: str, code: int = 1) -> None:
    print(json.dumps({"ok": False, "error": msg}), file=sys.stderr)
    sys.exit(code)


def load_json(path: Path, what: str, code: int = 2) -> dict:
    if not path.is_file():
        die(f"{what} missing: {path}", code)
    try:
        doc = json.loads(path.read_text())
    except ValueError as e:
        die(f"{what} is not JSON: {e}")
    if not isinstance(doc, dict):
        die(f"{what} is not a JSON object")
    return doc


# ---------------------------------------------------------------- http

class Client:
    def __init__(self, args, need: set[str]):
        self.key = etsy_auth.api_key_header()
        ns = argparse.Namespace(token_file=args.token_file)
        tok = etsy_auth.valid_token(ns)
        self.bearer = tok["access_token"]
        scopes = set((tok.get("scope") or "").split())
        missing = need - scopes
        if missing:
            die(f"token scope is '{tok.get('scope')}'; this command needs {sorted(need)}. "
                f"Re-login: etsy_auth.py --env store/.env login --scopes \"{' '.join(sorted(scopes | need))}\"", 3)
        self.user_id = int(self.bearer.split(".")[0])
        self.calls = 0
        self.remaining = None

    def _send(self, req: urllib.request.Request):
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                self.remaining = r.headers.get("x-remaining-today")
                body = r.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            text = e.read()[:600].decode(errors="replace")
            die(f"HTTP {e.code} on {req.get_method()} {urllib.parse.urlparse(req.full_url).path}: {text}")

    def get(self, path: str, params: dict | None = None):
        url = f"{API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        return self._send(urllib.request.Request(url, headers=self._headers()))

    def form(self, method: str, path: str, fields: dict):
        data = urllib.parse.urlencode({k: v for k, v in fields.items() if v is not None}).encode()
        req = urllib.request.Request(f"{API}{path}", data=data, method=method,
                                     headers={**self._headers(), "Content-Type": "application/x-www-form-urlencoded"})
        return self._send(req)

    def multipart(self, path: str, fields: dict, file_field: str, file_path: Path):
        boundary = f"----dapilot{uuid.uuid4().hex}"
        parts = []
        for k, v in fields.items():
            if v is None:
                continue
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
        ctype = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
                      f"filename=\"{file_path.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode())
        parts.append(file_path.read_bytes())
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        body = b"".join(parts)
        req = urllib.request.Request(f"{API}{path}", data=body, method="POST",
                                     headers={**self._headers(), "Content-Type": f"multipart/form-data; boundary={boundary}",
                                              "Content-Length": str(len(body))})
        return self._send(req)

    def _headers(self) -> dict:
        return {"x-api-key": self.key, "Authorization": f"Bearer {self.bearer}"}

    def quota(self) -> dict:
        return {"calls": self.calls, "remaining_today": self.remaining}


def shop_of(c: Client, args) -> int:
    if getattr(args, "shop_id", None):
        return int(args.shop_id)
    me = c.get("/users/me")
    sid = me.get("shop_id")
    if not sid:
        die("this Etsy user has no shop; pass --shop-id if you administer one")
    return int(sid)


# ---------------------------------------------------------------- commands

def cmd_shop(args) -> int:
    c = Client(args, {"shops_r"})
    sid = shop_of(c, args)
    shop = c.get(f"/shops/{sid}")
    print(json.dumps({"ok": True, "user_id": c.user_id, "shop_id": sid, "shop_name": shop.get("shop_name"),
                      "currency": shop.get("currency_code"), "url": shop.get("url"),
                      "listing_active_count": shop.get("listing_active_count"), "quota": c.quota()}))
    return 0


def cmd_profiles(args) -> int:
    c = Client(args, {"shops_r"})
    sid = shop_of(c, args)
    ship = c.get(f"/shops/{sid}/shipping-profiles").get("results") or []
    ret = c.get(f"/shops/{sid}/policies/return").get("results") or []
    ready = c.get(f"/shops/{sid}/readiness-state-definitions").get("results") or []
    print(json.dumps({
        "ok": True, "shop_id": sid,
        "shipping_profiles": [{"id": p.get("shipping_profile_id"), "title": p.get("title"),
                               "origin": p.get("origin_country_iso"), "processing_days": [p.get("min_processing_days"), p.get("max_processing_days")]} for p in ship],
        "return_policies": [{"id": p.get("return_policy_id"), "accepts_returns": p.get("accepts_returns"),
                             "accepts_exchanges": p.get("accepts_exchanges"), "return_deadline": p.get("return_deadline")} for p in ret],
        "readiness_states": [{"id": p.get("readiness_state_definition_id"), "state": p.get("readiness_state"),
                              "processing": [p.get("min_processing_time"), p.get("max_processing_time"), p.get("processing_time_unit")]} for p in ready],
        "quota": c.quota(),
    }, indent=1))
    return 0


def _check_copy(listing: dict) -> list[str]:
    errors = []
    title = listing.get("title") or ""
    if not title.strip():
        errors.append("title is empty")
    if len(title) > TITLE_MAX:
        errors.append(f"title is {len(title)} characters; Etsy allows {TITLE_MAX}")
    if not (listing.get("description") or "").strip():
        errors.append("description is empty")
    tags = listing.get("tags") or []
    if len(tags) > TAGS_MAX:
        errors.append(f"{len(tags)} tags; Etsy allows {TAGS_MAX}")
    for t in tags:
        if len(t) > TAG_LEN_MAX:
            errors.append(f"tag '{t}' is {len(t)} characters; Etsy allows {TAG_LEN_MAX}")
    if not isinstance(listing.get("taxonomy_id"), int):
        errors.append("taxonomy_id must be a seller taxonomy id (integer)")
    if listing.get("who_made") not in ("i_did", "someone_else", "collective"):
        errors.append("who_made must be i_did, someone_else or collective")
    if not listing.get("when_made"):
        errors.append("when_made is required")
    q = listing.get("quantity", 1)
    if not isinstance(q, int) or q < 1:
        errors.append("quantity must be a positive integer")
    return errors


def cmd_draft(args) -> int:
    wd = Path(args.workdir)
    listing = load_json(wd / "listing.json", "listing.json")
    price = load_json(wd / "price.json", "price.json")
    quote = load_json(wd / "quote.json", "quote.json")
    analysis = load_json(wd / "analysis.json", "analysis.json")
    pub_path = wd / "publish.json"
    pub = json.loads(pub_path.read_text()) if pub_path.is_file() else {}
    if pub.get("listing_id"):
        print(json.dumps({"ok": True, "cached": True, "listing_id": pub["listing_id"], "state": pub.get("state"),
                          "url": pub.get("url"), "note": "publish.json already names a listing; not creating a second draft"}))
        return 0
    errors = _check_copy(listing)
    if errors:
        die("listing.json: " + "; ".join(errors))
    amount = price.get("price")
    if not isinstance(amount, (int, float)) or amount <= 0:
        die("price.json has no positive price")

    c = sid = None
    if not args.dry_run:
        c = Client(args, {"listings_w"})
        sid = shop_of(c, args)
    bbox = analysis.get("bbox_mm") or []
    model_g = (quote.get("metrics") or {}).get("model_g")
    fields = {
        "quantity": int(listing.get("quantity", 1)),
        "title": listing["title"],
        "description": listing["description"],
        "price": round(float(amount), 2),
        "who_made": listing["who_made"],
        "when_made": listing["when_made"],
        "taxonomy_id": int(listing["taxonomy_id"]),
        "type": listing.get("type", "physical"),
        "is_supply": "true" if listing.get("is_supply") else "false",
        "should_auto_renew": "true" if listing.get("should_auto_renew") else "false",
        "tags": ",".join(listing.get("tags") or []) or None,
        "materials": ",".join(listing.get("materials") or []) or None,
        "shipping_profile_id": listing.get("shipping_profile_id"),
        "return_policy_id": listing.get("return_policy_id"),
        "readiness_state_id": listing.get("readiness_state_id"),
        "processing_min": listing.get("processing_min"),
        "processing_max": listing.get("processing_max"),
    }
    if isinstance(model_g, (int, float)) and model_g > 0:
        fields.update({"item_weight": round(float(model_g), 1), "item_weight_unit": "g"})
    if len(bbox) == 3 and all(isinstance(v, (int, float)) and v > 0 for v in bbox):
        fields.update({"item_length": round(float(bbox[1]), 1), "item_width": round(float(bbox[0]), 1),
                       "item_height": round(float(bbox[2]), 1), "item_dimensions_unit": "mm"})
    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "shop_id": sid, "fields": fields}, indent=1))
        return 0
    res = c.form("POST", f"/shops/{sid}/listings", fields)
    pub = {
        "platform": "etsy",
        "shop_id": sid,
        "listing_id": res.get("listing_id"),
        "state": res.get("state"),
        "url": res.get("url"),
        "title": res.get("title"),
        "price": res.get("price"),
        "quantity": res.get("quantity"),
        "taxonomy_id": res.get("taxonomy_id"),
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "images": [],
        "video": None,
        "activated": None,
    }
    pub_path.write_text(json.dumps(pub, indent=2))
    print(json.dumps({"ok": True, "cached": False, "listing_id": pub["listing_id"], "state": pub["state"],
                      "url": pub["url"], "out": str(pub_path), "quota": c.quota()}))
    return 0


def cmd_media(args) -> int:
    wd = Path(args.workdir)
    pub_path = wd / "publish.json"
    pub = load_json(pub_path, "publish.json")
    if not pub.get("listing_id"):
        die("publish.json has no listing_id; run draft first", 2)
    design = load_json(wd / "design.json", "design.json")
    listing = load_json(wd / "listing.json", "listing.json")
    alts = listing.get("alt_text") or {}
    photos = design.get("photos") or []
    if len(photos) > IMAGES_MAX:
        die(f"design.json lists {len(photos)} photos; Etsy allows {IMAGES_MAX}")
    c = Client(args, {"listings_w"})
    sid = int(pub.get("shop_id") or shop_of(c, args))
    lid = pub["listing_id"]
    done = {img["rank"]: img for img in pub.get("images") or []}
    uploaded, skipped = [], []
    for rank, p in enumerate(photos, start=1):
        if rank in done:
            skipped.append(rank)
            continue
        f = wd / p["file"]
        if not f.is_file():
            die(f"photo missing: {f}", 2)
        alt = (alts.get(p.get("id")) or listing.get("title") or "")[:ALT_MAX]
        res = c.multipart(f"/shops/{sid}/listings/{lid}/images", {"rank": rank, "alt_text": alt}, "image", f)
        rec = {"rank": rank, "id": p.get("id"), "file": p["file"], "listing_image_id": res.get("listing_image_id"),
               "url": res.get("url_fullxfull")}
        pub.setdefault("images", []).append(rec)
        pub_path.write_text(json.dumps(pub, indent=2))
        uploaded.append(rank)
        if args.limit and len(uploaded) >= args.limit:
            break
    video = design.get("video")
    video_state = "none"
    if video and not args.no_video:
        if pub.get("video"):
            video_state = "already uploaded"
        else:
            f = wd / video["file"]
            if not f.is_file():
                die(f"video missing: {f}", 2)
            res = c.multipart(f"/shops/{sid}/listings/{lid}/videos", {}, "video", f)
            pub["video"] = {"id": video.get("id"), "file": video["file"], "video_id": res.get("video_id"),
                            "state": res.get("video_state"), "url": res.get("video_url")}
            pub_path.write_text(json.dumps(pub, indent=2))
            video_state = "uploaded"
    print(json.dumps({"ok": True, "listing_id": lid, "uploaded_ranks": uploaded, "skipped_ranks": skipped,
                      "images_total": len(pub.get("images") or []), "video": video_state, "quota": c.quota()}))
    return 0


def cmd_status(args) -> int:
    wd = Path(args.workdir)
    pub = load_json(wd / "publish.json", "publish.json")
    lid = pub.get("listing_id")
    if not lid:
        die("publish.json has no listing_id", 2)
    c = Client(args, {"listings_r"})
    l = c.get(f"/listings/{lid}")
    imgs = c.get(f"/listings/{lid}/images").get("results") or []
    vids = c.get(f"/listings/{lid}/videos").get("results") or []
    print(json.dumps({"ok": True, "listing_id": lid, "state": l.get("state"), "title": l.get("title"),
                      "price": l.get("price"), "quantity": l.get("quantity"), "url": l.get("url"),
                      "taxonomy_id": l.get("taxonomy_id"), "shipping_profile_id": l.get("shipping_profile_id"),
                      "return_policy_id": l.get("return_policy_id"), "tags": l.get("tags"), "materials": l.get("materials"),
                      "images": [{"rank": i.get("rank"), "id": i.get("listing_image_id"), "px": [i.get("full_width"), i.get("full_height")]} for i in imgs],
                      "videos": [{"id": v.get("video_id"), "state": v.get("video_state")} for v in vids],
                      "quota": c.quota()}, indent=1))
    return 0


def cmd_activate(args) -> int:
    wd = Path(args.workdir)
    pub_path = wd / "publish.json"
    pub = load_json(pub_path, "publish.json")
    lid = pub.get("listing_id")
    if not lid:
        die("publish.json has no listing_id", 2)
    if not (args.by and args.reason):
        die("activation publishes on etsy.com and charges the listing fee; it needs --by <person> and --reason", 1)
    if not pub.get("images"):
        die("no images recorded in publish.json; Etsy refuses to activate a listing without an image", 2)
    c = Client(args, {"listings_w"})
    sid = int(pub.get("shop_id") or shop_of(c, args))
    res = c.form("PATCH", f"/shops/{sid}/listings/{lid}", {"state": "active"})
    pub["state"] = res.get("state")
    pub["activated"] = {"by": args.by, "reason": args.reason, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    pub_path.write_text(json.dumps(pub, indent=2))
    print(json.dumps({"ok": True, "listing_id": lid, "state": pub["state"], "url": res.get("url"), "quota": c.quota()}))
    return 0


# ---------------------------------------------------------------- cli

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", help="path to a .env file with the Etsy key")
    ap.add_argument("--token-file", help="OAuth token from etsy_auth.py; default $DAPILOT_STORE_DIR/etsy_token.json")
    ap.add_argument("--shop-id", type=int, help="default: the token owner's shop from /users/me")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("shop")
    sub.add_parser("profiles")
    d = sub.add_parser("draft"); d.add_argument("--workdir", required=True); d.add_argument("--dry-run", action="store_true")
    m = sub.add_parser("media"); m.add_argument("--workdir", required=True)
    m.add_argument("--limit", type=int, help="upload at most this many photos this run")
    m.add_argument("--no-video", action="store_true")
    s = sub.add_parser("status"); s.add_argument("--workdir", required=True)
    a = sub.add_parser("activate"); a.add_argument("--workdir", required=True)
    a.add_argument("--by"); a.add_argument("--reason")
    args = ap.parse_args()
    if args.env:
        etsy_auth.load_env(args.env)
    args.token_file = args.token_file or etsy_auth.default_token_file()
    if not args.token_file:
        die("DAPILOT_STORE_DIR is not set and no --token-file given", 2)
    return {"shop": cmd_shop, "profiles": cmd_profiles, "draft": cmd_draft, "media": cmd_media,
            "status": cmd_status, "activate": cmd_activate}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
