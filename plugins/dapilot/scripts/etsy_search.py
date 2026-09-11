#!/usr/bin/env python
"""Search Etsy for comparable listings and keep only tangible, physical products.

Deterministic. No judgement: this script drops what the data proves is not a
physical object (digital downloads, file bundles, supplies) and what is the
wrong size for the model being priced. Whether a surviving listing is really
the same kind of object is decided by the rules a model writes (rules.json)
and scored here by the `score` subcommand, never listing by listing.

Key-only calls need ETSY_KEYSTRING and ETSY_SHARED_SECRET (env or --env).
With an OAuth token file (etsy_auth.py login) the script also reads each kept
listing's inventory and picks the variant closest to the target size. The
token file and the taxonomy cache default to $DAPILOT_STORE_DIR/etsy_token.json
and $DAPILOT_STORE_DIR/reference/etsy_buyer_taxonomy.json; without that
variable the inventory pass is skipped and the taxonomy is cached under
~/.cache/dapilot/.

Moved from the repo's scripts/ on 2026-09-09 when research-comparables started
depending on it; the copy there is the development original.

    etsy_search.py search "duck charm" --noun duck --taxonomy-id 1799 \
        --analysis work/<hash>/analysis.json --out work/<hash>/research/etsy.json
    etsy_search.py score --full work/<hash>/research/etsy.full.json \
        --rules work/<hash>/research/rules.json --out work/<hash>/research/etsy.json
    etsy_search.py filter raw.json --size-mm 120,60,40 [--noun duck]
    etsy_search.py taxonomy find "desk toy"        # buyer taxonomy ids for --taxonomy-id
    etsy_search.py taxonomy path 1793

Why a price ladder and not offsets. Measured 2026-09-11: /listings/active
accepts offset 0 to 5 only for this key, bearer or not (HTTP 400 above that),
so one query yields its top 100 by relevance and nothing more. min_price and
max_price windows do work: each window returns its own relevance top 100 in
the requested currency, and a geometric USD ladder over "duck charm" returned
about 700 listings. Price is therefore the paging axis.

Ladder mode (search with --noun). --ladder-edges (default
"0,2,4,8,16,32,64,128,256", in --currency) defines the windows [0,2), [2,4),
... [128,256), [256, open). Each window is one search call with
min_price/max_price, sort_on=score, limit --page-size (100, the Etsy maximum),
offset 0, and is recorded as a page: {page, min_price, max_price (null for the
open top), count (Etsy's matches in the window), fetched, survived, hit_rate}.
Listings are deduplicated by listing_id across windows, first wins. After each
window the physical and size filters and the noun floor run over it and its
hit rate is survivors / fetched. A window is skipped, recorded with
"skipped": true, when the --stop-pages (2) windows below it were both under
--stop-hit-rate (0.2) and at least one survivor was seen earlier; once
skipping starts every window above is skipped too, because a dry stretch of
the ladder does not become wet again by pricing higher. --limit (1000) caps
the survivors kept across windows. "stop_reason" is "ladder_done" when every
window was visited or skipped and "limit" when the cap ended the read.
Images, shop stats and the inventory pass are fetched for survivors only,
after the ladder, so a deep read costs at most one search call and one batch
call per window (18 with the default ladder) plus one inventory call per
survivor with variants. A narrow aisle costs about 20 calls, a broad query
with 300 survivors can cost over 300; the quota is 10,000 calls a day and
the stdout summary reports quota.calls for each read.

Without --noun the old offset loop runs: no noun floor, no stop rule, --limit
defaults to 100 (the one page Etsy allows), the read ends at --limit, a short
page ("exhausted") or Etsy's offset refusal ("offset_cap"), and every listing
is enriched so a --raw dump can be re-filtered.

Output directories (--out, its .full.json sibling, --raw) are created before
the first Etsy call, so a fresh work dir with no research/ works.

The noun floor: the product noun must appear in the title as a word,
case-insensitive, singular or plural ("duck" matches "ducks", "bunny" matches
"bunnies"). Pass the singular. Listings without it are dropped with reason
"noun_missing".

Score (etsy_search.py score --full F --rules R --out C). Reads rules.json:
{"noun", "required_any": [...], "excluded": [...], "materials_allowed": [...],
"materials_excluded": [...], "taxonomy_id", "notes"} (the selection threshold is the
script default, 0.6; the model never writes one) and
adds "relevance" and "market_weight" to every kept record in the full file,
then rewrites the compact file from it. Both formulas are plain functions
(relevance, market_weight) that market_stats.py imports.

    relevance, 0 to 1, a weighted sum of five tests:
        0.30  noun in the title
        0.20  any required_any word in title or tags (full credit if the list is empty)
        0.20  no excluded word in title or tags; a hit scores 0 and is listed in "excluded_by"
        0.15  an allowed material in materials, title or tags;
              an excluded material anywhere in those forces relevance to 0
        0.15  size: 0.15 x (1 - min(1, |log2(size_ratio)|)) when size_ratio is
              known, else 0.075 (half credit for unknown)
    market_weight, >= 0, how much the price says about what buyers pay:
        log1p(sold) + 0.5 x log1p(favorers) + (1 if sold > 0 else 0)
        sold is shop.sold when present, else top-level sold; favorers is
        num_favorers when present, else favorers; null, missing or boolean
        values count as 0. No sales, no favourites: 0. Thousands of sales
        and hundreds of favourites: about 8 to 10.
    Both scores are rounded to 3 decimals.

Words in rules are matched as whole words, case-insensitive, plural tolerant
(duck/ducks, box/boxes, bunny/bunnies), multi-word phrases allowed ("set of",
"3d printed") with a space also matching a hyphen. Digits may sit directly
left of a word ("10pcs" matches "pcs"); letters may not. Blank rule entries
are dropped before use and an empty required_any earns its credit.

Search only honours ids from the *buyer* taxonomy; a parent id includes its
children; an unknown id is silently ignored by Etsy, so this script refuses it.

Output JSON (the .full.json): {query, noun, currency, target_mm, total_matches,
fetched, pages: [{page, min_price, max_price, count, fetched, survived, hit_rate,
skipped?}] (or {page, offset, fetched, survived, hit_rate} without --noun), stop_reason,
kept: [projected records, each with "page"], dropped: [{listing_id, url, reason}],
quota}. The compact --out file carries compact records, "dropped" as a count
and "full" naming the full file.
Exit 0 with kept results, 2 when nothing survived, 1 on error.
"""
import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = "https://openapi.etsy.com/v3/application"
BATCH = 100  # max ids per /listings/batch call
PAGE_MAX = 100  # max limit per /listings/active call
LADDER_EDGES = "0,2,4,8,16,32,64,128,256"  # geometric: each window is one relevance top page
LIMIT_LADDER = 1000  # survivors kept across windows with --noun
LIMIT_OFFSET = 100  # listings fetched by offset without --noun; Etsy refuses deeper offsets

RELEVANCE_WEIGHTS = {"noun": 0.30, "required": 0.20, "excluded": 0.20, "material": 0.15, "size": 0.15}

def default_token_file():
    d = os.environ.get("DAPILOT_STORE_DIR")
    return os.path.join(d, "etsy_token.json") if d else None


def default_taxonomy_cache():
    d = os.environ.get("DAPILOT_STORE_DIR")
    if d:
        return os.path.join(d, "reference", "etsy_buyer_taxonomy.json")
    return os.path.join(os.path.expanduser("~"), ".cache", "dapilot", "etsy_buyer_taxonomy.json")
DIGITAL_TITLE = re.compile(
    r"\b(stl|3mf|obj)\b\s*(file|files|bundle|pack|download)?"
    r"|digital (download|file|files|product)"
    r"|3d print(ing)? files?\b"
    r"|files? only\b"
    r"|instant download",
    re.I,
)
SIZE_PROPERTY = re.compile(r"size|dimension|length|height|width|scale|tall|inch|\bcm\b|\bmm\b", re.I)
SIZE_VALUE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(mm|cm|in|inch|inches|\"|”|ft|m)\b", re.I)
UNIT_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "inch": 25.4, "inches": 25.4, '"': 25.4, "”": 25.4,
           "ft": 304.8, "m": 1000.0}
UNIT_G = {"g": 1.0, "kg": 1000.0, "oz": 28.3495, "lb": 453.592}


def die(msg, code=1):
    print(json.dumps({"ok": False, "error": msg}), file=sys.stderr)
    sys.exit(code)


def load_env(path):
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def api_key():
    ks, sec = os.environ.get("ETSY_KEYSTRING"), os.environ.get("ETSY_SHARED_SECRET")
    if not ks or not sec:
        die("ETSY_KEYSTRING and ETSY_SHARED_SECRET must be set (or pass --env store/.env)")
    return f"{ks}:{sec}"


def get(path, params, key, quota, bearer=None, raise_http=False):
    """GET with the quota headers recorded; retries one 429; dies on other errors unless raise_http."""
    url = f"{BASE}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    headers = {"x-api-key": key}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(2):
        # Counted before the request so a failed call is still a spent call.
        quota["calls"] = quota.get("calls", 0) + 1
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                quota["remaining_today"] = r.headers.get("x-remaining-today")
                quota["limit_per_day"] = r.headers.get("x-limit-per-day")
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(int(e.headers.get("retry-after", "2")))
                continue
            if raise_http:
                raise
            die(f"HTTP {e.code} on {path}: {e.read()[:300].decode(errors='replace')}")
    die("rate limited twice")


# ---------------------------------------------------------------- taxonomy

def load_taxonomy(cache, key, quota):
    """Buyer taxonomy as {id: {"path": [...], "parent_id": ...}}; fetched once, cached."""
    if not os.path.exists(cache):
        data = get("/buyer-taxonomy/nodes", {}, key, quota)
        os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
        json.dump(data, open(cache, "w"))
    data = json.load(open(cache))
    flat = {}

    def walk(n, path):
        p = path + [n["name"]]
        flat[n["id"]] = {"path": p, "parent_id": n.get("parent_id"), "level": n.get("level")}
        for c in n.get("children", []):
            walk(c, p)

    for n in data["results"]:
        walk(n, [])
    return flat


def cmd_taxonomy(args, key, quota):
    tax = load_taxonomy(args.taxonomy_cache, key, quota)
    if args.what == "find":
        needle = args.term.lower()
        hits = [(i, " > ".join(n["path"])) for i, n in tax.items() if needle in " > ".join(n["path"]).lower()]
        hits.sort(key=lambda h: (len(h[1]), h[1]))
        for i, p in hits[: args.max]:
            print(f"{i:>6}  {p}")
        if not hits:
            sys.exit(2)
    else:
        n = tax.get(int(args.term))
        if not n:
            die(f"{args.term} is not a buyer taxonomy id (search ignores seller-only ids)")
        print(json.dumps({"id": int(args.term), "path": n["path"], "parent_id": n["parent_id"]}))


# ---------------------------------------------------------------- words

_WORD_CACHE = {}


def word_pattern(word):
    """Whole-word, case-insensitive, plural-tolerant matcher for a rule word or phrase."""
    w = (word or "").strip().lower()
    if w not in _WORD_CACHE:
        # A space in a phrase also matches a hyphen: "3d printed" finds "3D-printed".
        esc = re.escape(w).replace(r"\ ", r"[\s\-]+")
        alts = [esc + r"(?:s|es)?"]
        if w.endswith("y") and len(w) > 2:
            alts.append(esc[:-1] + "ies")
        # The lookbehind excludes letters only: Etsy titles glue counts to units
        # ("10pcs", "2pack"), and "pcs" must still match there. Letters on the
        # right still end the match so "duck" does not find "duckling".
        _WORD_CACHE[w] = re.compile(r"(?<![a-z])(?:" + "|".join(alts) + r")(?![a-z0-9])", re.I)
    return _WORD_CACHE[w]


def has_word(text, word):
    return isinstance(word, str) and bool(word.strip()) and bool(word_pattern(word).search(text or ""))


def rule_words(rules, field):
    """The non-blank string entries of a rules list; anything else is dropped."""
    return [w for w in (rules.get(field) or []) if isinstance(w, str) and w.strip()]


def number(v):
    """v as a non-negative float when it is a real number; booleans and nulls are 0."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else 0.0


def noun_in_title(l, noun):
    return has_word(l.get("title") or "", noun)


# ---------------------------------------------------------------- search + enrich

def parse_ladder(text):
    """--ladder-edges as [(min, max|None), ...]: strictly increasing, non-negative edges."""
    try:
        edges = [float(x) for x in text.split(",") if x.strip()]
    except ValueError:
        die(f"--ladder-edges must be comma-separated numbers, got {text!r}")
    if not edges or edges[0] < 0 or any(b <= a for a, b in zip(edges, edges[1:])):
        die(f"--ladder-edges must be non-negative and strictly increasing, got {text!r}")
    return [(a, b) for a, b in zip(edges, edges[1:] + [None])]


def plain(x):
    """A price edge for a query string or a record: 2 not 2.0, 2.5 stays 2.5."""
    return None if x is None else (int(x) if float(x).is_integer() else x)


def fetch_ladder(keywords, args, target, noun, key, quota):
    """One relevance top page per price window, filtered as it lands.

    Returns (survivors, rejected, dropped, pages, stop_reason, total) in the
    shape fetch_pages returns, with pages keyed by price window instead of
    offset and total the sum of Etsy's per-window counts.
    """
    survivors, rejected, dropped, pages, seen = [], [], [], [], set()
    total, low_streak, seen_wet = 0, 0, False
    stop_reason = "ladder_done"
    for i, (lo, hi) in enumerate(parse_ladder(args.ladder_edges), start=1):
        entry = {"page": i, "min_price": plain(lo), "max_price": plain(hi)}
        if seen_wet and low_streak >= args.stop_pages:
            # stop_pages dry windows in a row, after at least one wet window
            # (hit rate at or above the threshold): the rest of the ladder is
            # priced past our product. Empty windows are neither wet nor dry,
            # and a stray survivor in a dry window does not arm the rule.
            # Recorded so the read can be audited.
            pages.append({**entry, "count": None, "fetched": 0, "survived": 0, "hit_rate": None,
                          "skipped": True})
            continue
        params = {"keywords": keywords, "limit": args.page_size, "offset": 0, "sort_on": "score",
                  "currency": args.currency}
        if lo > 0:
            params["min_price"] = plain(lo)
        if hi is not None:
            params["max_price"] = plain(hi)
        if args.taxonomy_id:
            params["taxonomy_id"] = args.taxonomy_id
        page = get("/listings/active", params, key, quota)
        results = page.get("results") or []
        count = page.get("count") or 0
        total += count
        fresh = []
        for l in results:
            # Windows share their edges and Etsy repeats listings; the first copy wins.
            if l["listing_id"] in seen:
                continue
            seen.add(l["listing_id"])
            l["page"] = i
            fresh.append(l)
        s, r, d = prefilter(fresh, target, args, noun)
        room = args.limit - len(survivors)
        if len(s) > room:
            s = s[:room]
        survivors += s
        rejected += r
        dropped += d
        # Rate over the listings new to this read: copies already seen in a
        # lower window (Etsy's edges are inclusive) would only dilute it.
        hit_rate = round(len(s) / len(fresh), 3) if fresh else None
        pages.append({**entry, "count": count, "fetched": len(results), "new": len(fresh),
                      "survived": len(s), "hit_rate": hit_rate})
        if hit_rate is None:
            pass  # empty window: no evidence either way
        elif hit_rate >= args.stop_hit_rate:
            seen_wet, low_streak = True, 0
        else:
            low_streak += 1
        if len(survivors) >= args.limit:
            stop_reason = "limit"
            break
    return survivors, rejected, dropped, pages, stop_reason, total


def fetch_pages(keywords, args, target, noun, key, quota):
    """Page loop by offset in relevance order; filters each page as it lands.

    Returns (survivors, rejected, dropped, pages, stop_reason, total). survivors
    and rejected are raw listings tagged with "page"; dropped is the reason list
    for the output. Only --limit, a short page or Etsy's offset cap ends the
    loop, which is the old single-fetch behaviour used without a noun.
    """
    survivors, rejected, dropped, pages, seen = [], [], [], [], set()
    offset, total = 0, 0
    stop_reason = "exhausted"
    while True:
        want = min(args.page_size, args.limit - offset)
        if want <= 0:
            stop_reason = "limit"
            break
        params = {"keywords": keywords, "limit": want, "offset": offset, "sort_on": "score",
                  "currency": args.currency}
        if args.taxonomy_id:
            params["taxonomy_id"] = args.taxonomy_id
        try:
            page = get("/listings/active", params, key, quota, raise_http=True)
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode(errors="replace")
            # Measured 2026-09-11: Etsy refuses offset > 5 on /listings/active for
            # this key with or without an OAuth bearer ("anonymous requests"), so
            # a deep read ends here. Recorded, never silent.
            if e.code == 400 and offset > 0 and "offset" in body.lower():
                stop_reason = "offset_cap"
                break
            die(f"HTTP {e.code} on /listings/active: {body}")
        results = page.get("results") or []
        total = page.get("count") or 0
        n = len(pages) + 1
        fresh = []
        for l in results:
            # Etsy occasionally repeats a listing across offsets; the first copy wins.
            if l["listing_id"] in seen:
                continue
            seen.add(l["listing_id"])
            l["page"] = n
            fresh.append(l)
        s, r, d = prefilter(fresh, target, args, noun)
        survivors += s
        rejected += r
        dropped += d
        offset += len(results)
        if not results:
            break
        hit_rate = round(len(s) / len(results), 3)
        pages.append({"page": n, "offset": offset - len(results), "fetched": len(results),
                      "survived": len(s), "hit_rate": hit_rate})
        if len(results) < want or offset >= total:
            stop_reason = "exhausted"
            break
        if offset >= args.limit:
            stop_reason = "limit"
            break
    return survivors, rejected, dropped, pages, stop_reason, total


def enrich(listings, currency, key, quota):
    """Attach images and shop stats; one batch call per 100 listings."""
    out = []
    ids = [l["listing_id"] for l in listings]
    for i in range(0, len(ids), BATCH):
        page = get("/listings/batch", {
            "listing_ids": ",".join(map(str, ids[i:i + BATCH])),
            "includes": "Images,Shop", "currency": currency,
        }, key, quota)
        out.extend(page["results"])
    by_id = {l["listing_id"]: l for l in out}
    merged = []
    for l in listings:
        e = by_id.get(l["listing_id"]) or l
        if "page" in l:
            e["page"] = l["page"]  # the batch record replaces the search record; keep its page
        merged.append(e)
    return merged


# ---------------------------------------------------------------- units

def price_of(l):
    p = l.get("converted_price") or l.get("price")
    if not p:
        return None, None
    return round(p["amount"] / p["divisor"], 2), p["currency_code"]


def dims_mm(l):
    # Etsy can ship item_length with item_width or item_height null (seen on a
    # deep "duck charm" read, 2026-09-11); a partial box is no box.
    dims = [l.get(k) for k in ("item_length", "item_width", "item_height")]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in dims):
        return None
    f = UNIT_MM.get(l.get("item_dimensions_unit") or "")
    if not f:
        return None
    return sorted([round(float(v) * f, 1) for v in dims], reverse=True)


def weight_g(l):
    if not l.get("item_weight") or not l.get("item_weight_unit"):
        return None
    f = UNIT_G.get(l["item_weight_unit"])
    return round(float(l["item_weight"]) * f, 1) if f else None


def size_from_text(text):
    """Largest length with a unit mentioned in a variant label, in mm, or None."""
    best = None
    for num, unit in SIZE_VALUE.findall(text or ""):
        mm = float(num.replace(",", ".")) * UNIT_MM.get(unit.lower(), UNIT_MM.get(unit, 0))
        if mm and (best is None or mm > best):
            best = mm
    return best


# ---------------------------------------------------------------- inventory (OAuth)

def bearer_token(token_file):
    if not token_file or not os.path.exists(token_file):
        return None
    try:
        import etsy_auth
        ns = argparse.Namespace(token_file=token_file)
        return etsy_auth.valid_token(ns)["access_token"]
    except SystemExit:
        return None


def fx_rate(l):
    """Shop currency -> requested currency, from the listing's own price pair."""
    p, c = l.get("price"), l.get("converted_price")
    if not p or not c or p["currency_code"] == c["currency_code"] or not p["amount"]:
        return 1.0, (c or p or {}).get("currency_code")
    return (c["amount"] / c["divisor"]) / (p["amount"] / p["divisor"]), c["currency_code"]


def variants_of(listing_id, rate, currency, key, bearer, quota):
    """Inventory products as variants, prices converted with the listing's fx rate."""
    inv = get(f"/listings/{listing_id}/inventory", {}, key, quota, bearer=bearer)
    out = []
    for p in inv.get("products", []):
        if p.get("is_deleted"):
            continue
        offers = [o for o in p.get("offerings", []) if o.get("is_enabled", True) and not o.get("is_deleted")]
        if not offers:
            continue
        price = min(o["price"]["amount"] / o["price"]["divisor"] for o in offers)
        props = {v["property_name"]: ", ".join(v.get("values") or []) for v in p.get("property_values", [])}
        size_props = {k: v for k, v in props.items() if SIZE_PROPERTY.search(k) or SIZE_VALUE.search(v)}
        out.append({"props": props, "price": round(price * rate, 2), "currency": currency,
                    "shop_price": round(price, 2), "shop_currency": offers[0]["price"]["currency_code"],
                    "has_size_property": bool(size_props),
                    "size_mm": size_from_text(" ".join(size_props.values()))})
    return out


def choose_variant(variants, target_mm):
    """Closest variant by parsed size; else the cheapest, flagged when sizes exist but are unparseable."""
    sized = [v for v in variants if v["size_mm"]]
    if sized and target_mm:
        v = min(sized, key=lambda v: abs(v["size_mm"] - target_mm[0]))
        return v, "variant_size_match"
    cheapest = min(variants, key=lambda v: v["price"])
    if any(v["has_size_property"] for v in variants):
        return cheapest, "variant_unsized"  # e.g. Small/Medium/Large: price is the smallest size
    return cheapest, "variant_min"


# ---------------------------------------------------------------- filter + project

def reject_reason(l, target_mm, ratio_lo, ratio_hi):
    if l.get("state") not in (None, "active"):
        return "inactive"
    if l.get("listing_type") != "physical":
        return f"listing_type={l.get('listing_type')}"
    if l.get("file_data"):
        return "has_file_data"
    if l.get("is_supply"):
        return "is_supply"
    if DIGITAL_TITLE.search(l.get("title") or ""):
        return "digital_title"
    if target_mm:
        d = dims_mm(l)
        if d:
            ratio = d[0] / target_mm[0]
            if not (ratio_lo <= ratio <= ratio_hi):
                return f"size_ratio={ratio:.2f}"
    return None


def prefilter(listings, target, args, noun):
    """The data-only filters plus the noun floor. Needs nothing from enrichment.

    Returns (survivors, rejected, dropped): raw listings that passed, raw
    listings that did not, and the {listing_id, url, reason} rows for the latter.
    """
    survivors, rejected, dropped = [], [], []
    for l in listings:
        why = reject_reason(l, target, args.size_min_ratio, args.size_max_ratio)
        if not why and noun and not noun_in_title(l, noun):
            why = "noun_missing"
        if why:
            rejected.append(l)
            dropped.append({"listing_id": l["listing_id"], "url": l.get("url"), "reason": why})
        else:
            survivors.append(l)
    return survivors, rejected, dropped


def target_of(args):
    if args.analysis:
        return sorted(json.load(open(args.analysis))["bbox_mm"], reverse=True)
    if args.size_mm:
        return sorted((float(x) for x in args.size_mm.split(",")), reverse=True)
    return None


def project(l, target_mm, desc_chars):
    """The record the model sees. Everything else stays in the raw dump."""
    d = dims_mm(l)
    price, cur = price_of(l)
    shop = l.get("shop") or {}
    imgs = l.get("images") or []
    return {
        "listing_id": l["listing_id"],
        "url": l.get("url"),
        "title": l.get("title"),
        "description": (l.get("description") or "")[:desc_chars],
        "tags": l.get("tags") or [],
        "materials": l.get("materials") or [],
        "price": price,
        "currency": cur,
        "price_source": "listing",
        "price_is_base_variant": bool(l.get("has_variations")),
        "price_range": None,
        "variants": None,
        "variant_chosen": None,
        "num_favorers": l.get("num_favorers"),
        "views": l.get("views"),
        "quantity": l.get("quantity"),
        "who_made": l.get("who_made"),
        "when_made": l.get("when_made"),
        "processing_days": [l.get("processing_min"), l.get("processing_max")],
        "taxonomy_id": l.get("taxonomy_id"),
        "dims_mm": d,
        "dims_source": "listing" if d else None,
        "size_ratio": round(d[0] / target_mm[0], 2) if (d and target_mm) else None,
        "weight_g": weight_g(l),
        "shop": {
            "name": shop.get("shop_name"),
            "review_count": shop.get("review_count"),
            "review_average": shop.get("review_average"),
            "sold": shop.get("transaction_sold_count"),
            "country": shop.get("shop_location_country_iso"),
        },
        "images": [i.get("url_570xN") or i.get("url_fullxfull") for i in imgs[:3]],
        "created": l.get("original_creation_timestamp"),
        "page": l.get("page"),
    }


def compact_record(k):
    """What a model needs to judge a listing: one line of facts, not the whole record."""
    rec = {
        "listing_id": k["listing_id"], "url": k["url"], "title": k["title"],
        "price": k["price"], "currency": k["currency"], "price_source": k["price_source"],
        "price_range": k.get("price_range"),
        "dims_mm": k.get("dims_mm"), "size_ratio": k.get("size_ratio"), "weight_g": k.get("weight_g"),
        "materials": (k.get("materials") or [])[:4], "tags": (k.get("tags") or [])[:6],
        "sold": (k.get("shop") or {}).get("sold"), "reviews": (k.get("shop") or {}).get("review_count"),
        "favorers": k.get("num_favorers"), "quantity": k.get("quantity"),
        "description": (k.get("description") or "")[:160],
        "image": (k.get("images") or [None])[0],
        "page": k.get("page"),
    }
    for f in ("relevance", "market_weight", "excluded_by"):
        if f in k:
            rec[f] = k[f]
    return rec


def apply_inventory(rec, listing, target_mm, ratio_lo, ratio_hi, key, bearer, quota):
    """Replace the base price with the right variant's. Returns a drop reason or None."""
    rate, currency = fx_rate(listing)
    variants = variants_of(rec["listing_id"], rate, currency, key, bearer, quota)
    if not variants:
        return None
    rec["variants"] = variants
    rec["price_range"] = [min(v["price"] for v in variants), max(v["price"] for v in variants)]
    v, how = choose_variant(variants, target_mm)
    rec["variant_chosen"] = v
    rec["price"], rec["currency"], rec["price_source"] = v["price"], v["currency"], how
    rec["price_is_base_variant"] = how == "variant_unsized"
    if how == "variant_size_match":
        rec["dims_mm"], rec["dims_source"] = [v["size_mm"]], "variant"
        rec["size_ratio"] = round(v["size_mm"] / target_mm[0], 2)
        if not (ratio_lo <= rec["size_ratio"] <= ratio_hi):
            return f"size_ratio={rec['size_ratio']:.2f} (closest variant)"
    return None


def run_filter(survivors, dropped, args, query, total, quota, key=None, bearer=None,
               fetched=None, pages=None, stop_reason=None, noun=None):
    """Project the prefiltered survivors, read their inventory, assemble the output."""
    target = target_of(args)
    kept, dropped = [], list(dropped)
    for l in survivors:
        rec = project(l, target, args.desc_chars)
        if bearer and l.get("has_variations"):
            why = apply_inventory(rec, l, target, args.size_min_ratio, args.size_max_ratio, key, bearer, quota)
            if why:
                dropped.append({"listing_id": l["listing_id"], "url": l.get("url"), "reason": why})
                continue
        kept.append(rec)
    return {
        "ok": True,
        "query": query,
        "noun": noun,
        "taxonomy_id": getattr(args, "taxonomy_id", None),
        "currency": args.currency,
        "target_mm": target,
        "size_ratio_window": [args.size_min_ratio, args.size_max_ratio] if target else None,
        "total_matches": total,
        "fetched": len(survivors) + len(dropped) if fetched is None else fetched,
        "pages": pages or [],
        "stop_reason": stop_reason,
        "inventory_read": bool(bearer),
        "kept": kept,
        "dropped": dropped,
        "quota": quota,
    }


def pages_of(listings, survivor_ids):
    """Page table for listings that already carry a page marker (the filter subcommand)."""
    by_page = {}
    for l in listings:
        p = l.get("page") or 1
        by_page.setdefault(p, [0, 0])
        by_page[p][0] += 1
        by_page[p][1] += l["listing_id"] in survivor_ids
    pages, offset = [], 0
    for p in sorted(by_page):
        fetched, survived = by_page[p]
        pages.append({"page": p, "offset": offset, "fetched": fetched, "survived": survived,
                      "hit_rate": round(survived / fetched, 3)})
        offset += fetched
    return pages


def full_path_of(out_path):
    return os.path.splitext(out_path)[0] + ".full.json"


def ensure_parent(path):
    """Create the directory a file will be written to. Done before any Etsy
    call so a fresh work dir with no research/ cannot waste quota on an
    unwritable result."""
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)


def write_outputs(result, out_path, quota):
    """The model reads --out; keep it small (a 45 KB file cost a worker its whole
    budget on 2026-09-10). Everything projected goes to <out>.full.json."""
    full_path = full_path_of(out_path)
    ensure_parent(out_path)
    ensure_parent(full_path)
    open(full_path, "w").write(json.dumps(result, indent=1, ensure_ascii=False))
    compact = {**result, "kept": [compact_record(k) for k in result["kept"]],
               "dropped": len(result["dropped"]), "full": os.path.basename(full_path)}
    open(out_path, "w").write(json.dumps(compact, indent=1, ensure_ascii=False))
    pages = result.get("pages") or []
    print(json.dumps({"ok": True, "kept": len(result["kept"]), "dropped": len(result["dropped"]),
                      "pages": len(pages), "pages_skipped": sum(1 for p in pages if p.get("skipped")),
                      "stop_reason": result.get("stop_reason"),
                      "inventory_read": result["inventory_read"], "out": out_path, "full": full_path,
                      "quota": quota}))


# ---------------------------------------------------------------- scoring

def _text(rec, *fields):
    parts = []
    for f in fields:
        v = rec.get(f)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            parts.extend(str(x) for x in v)
    return " ".join(parts)


def relevance(rec, rules):
    """How much this listing is our product, 0 to 1, from rules.json. Returns (score, excluded_by).

    Weighted sum, weights in RELEVANCE_WEIGHTS (they add up to 1.0):
      noun      0.30  rules["noun"] appears in the title as a word (plural tolerant)
      required  0.20  any rules["required_any"] word in title or tags; an empty
                      list requires nothing and earns the credit
      excluded  0.20  no rules["excluded"] word in title or tags; a hit earns 0
                      and the words are returned in excluded_by
      material  0.15  a rules["materials_allowed"] word in materials, title or tags;
                      a rules["materials_excluded"] word in any of those makes the
                      whole score 0 (a wooden duck is not a printed duck) and is
                      returned in excluded_by as "material:<word>"
      size      0.15 x (1 - min(1, |log2(size_ratio)|)) when size_ratio is a
                      positive number; a size unknown earns half, 0.075
    Accepts a full record (tags, materials, size_ratio, title) or a compact one.
    """
    title = rec.get("title") or ""
    text = _text(rec, "title", "tags")
    mat_text = _text(rec, "title", "tags", "materials")
    excluded_by = [w for w in rule_words(rules, "excluded") if has_word(text, w)]
    banned = [w for w in rule_words(rules, "materials_excluded") if has_word(mat_text, w)]
    if banned:
        return 0.0, excluded_by + [f"material:{w}" for w in banned]
    w = RELEVANCE_WEIGHTS
    score = 0.0
    if has_word(title, rules.get("noun")):
        score += w["noun"]
    required = rule_words(rules, "required_any")
    if not required or any(has_word(text, r) for r in required):
        score += w["required"]
    if not excluded_by:
        score += w["excluded"]
    if any(has_word(mat_text, m) for m in rule_words(rules, "materials_allowed")):
        score += w["material"]
    ratio = number(rec.get("size_ratio"))
    if ratio > 0:
        score += w["size"] * (1 - min(1.0, abs(math.log2(ratio))))
    else:
        score += w["size"] / 2
    return round(min(1.0, score), 3), excluded_by


def market_weight(rec):
    """How much this listing's price says about what buyers pay, >= 0.

        log1p(sold) + 0.5 x log1p(favorers) + (1 if sold > 0 else 0)

    sold is the shop's lifetime sales, favorers the listing's favourites. sold
    is shop.sold when present, else top-level sold; favorers is num_favorers
    when present, else favorers; null, missing and boolean values count as 0.
    A listing with no evidence of sales weighs 0; a shop with thousands of
    sales and a listing with hundreds of favourites weighs 8 to 10. Accepts a
    full record (shop.sold, num_favorers) or a compact one (sold, favorers).
    """
    shop = rec.get("shop") if isinstance(rec.get("shop"), dict) else {}
    # "Present" means a non-null value: a full record with shop.sold null and no
    # top-level sold scores the same as a compact record with sold null.
    sold = number(shop.get("sold") if shop.get("sold") is not None else rec.get("sold"))
    fav = number(rec.get("num_favorers") if rec.get("num_favorers") is not None else rec.get("favorers"))
    return round(math.log1p(sold) + 0.5 * math.log1p(fav) + (1 if sold > 0 else 0), 3)


def cmd_score(args):
    try:
        full = json.load(open(args.full))
        rules = json.load(open(args.rules))
    except (OSError, ValueError) as e:
        die(f"cannot read input: {e}")
    if not isinstance(full.get("kept"), list) or not isinstance(full.get("dropped"), list):
        die(f"{args.full} is not a full search result (needs kept and dropped lists)")
    if not rules.get("noun"):
        die(f"{args.rules} has no noun")
    for k in full["kept"]:
        k["relevance"], excluded_by = relevance(k, rules)
        k["market_weight"] = market_weight(k)
        if excluded_by:
            k["excluded_by"] = excluded_by
        else:
            k.pop("excluded_by", None)
    full["rules"] = rules
    ensure_parent(args.out)
    open(args.full, "w").write(json.dumps(full, indent=1, ensure_ascii=False))
    compact = {**full, "kept": [compact_record(k) for k in full["kept"]],
               "dropped": len(full["dropped"]), "full": os.path.basename(args.full)}
    open(args.out, "w").write(json.dumps(compact, indent=1, ensure_ascii=False))
    rel = [k["relevance"] for k in full["kept"]]
    wt = [k["market_weight"] for k in full["kept"]]
    thr = rules.get("min_relevance", 0.6)  # market_stats.py applies the same default
    print(json.dumps({
        "ok": True, "n": len(rel), "out": args.out, "full": args.full,
        "relevance": {"min": min(rel), "max": max(rel)} if rel else None,
        "market_weight": {"min": min(wt), "max": max(wt)} if wt else None,
        "n_above_min_relevance": sum(r >= thr for r in rel) if isinstance(thr, (int, float)) else None,
        "n_excluded": sum("excluded_by" in k for k in full["kept"]),
    }))
    sys.exit(0 if rel else 2)


# ---------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", help="path to a .env file to read the Etsy key from")
    ap.add_argument("--token-file", help="OAuth token from etsy_auth.py; enables the inventory pass. Default: $DAPILOT_STORE_DIR/etsy_token.json")
    ap.add_argument("--no-inventory", action="store_true", help="skip the per-listing inventory calls even if a token exists")
    ap.add_argument("--taxonomy-cache", help="default: $DAPILOT_STORE_DIR/reference/etsy_buyer_taxonomy.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--analysis", help="work/<hash>/analysis.json; its bbox_mm sets the size window")
        p.add_argument("--size-mm", help="target size as L,W,H in mm, if no analysis.json")
        p.add_argument("--size-min-ratio", type=float, default=0.5, help="drop if longest edge / target < this")
        p.add_argument("--size-max-ratio", type=float, default=2.0, help="drop if longest edge / target > this")
        p.add_argument("--noun", help="product noun that must appear in the title (singular; plurals match); enables adaptive paging")
        p.add_argument("--currency", default="USD")
        p.add_argument("--desc-chars", type=int, default=600)
        p.add_argument("--out", help="write the compact result here and the full one to <out>.full.json instead of stdout")

    s = sub.add_parser("search", help="query Etsy window by window, filter, enrich survivors")
    s.add_argument("keywords")
    s.add_argument("--limit", type=int, help="with --noun: most survivors to keep across windows (default 1000); "
                                            "without: most listings to fetch by offset (default 100)")
    s.add_argument("--page-size", type=int, default=PAGE_MAX, help=f"listings per search call, 1 to {PAGE_MAX}")
    s.add_argument("--ladder-edges", default=LADDER_EDGES,
                   help=f"price window edges in --currency; the last window is open at the top (default {LADDER_EDGES})")
    s.add_argument("--stop-hit-rate", type=float, default=0.2, help="a window under this survivor share counts as dry (with --noun)")
    s.add_argument("--stop-pages", type=int, default=2, help="skip the rest of the ladder after this many consecutive dry windows (with --noun)")
    s.add_argument("--taxonomy-id", type=int, help="buyer taxonomy id; parents include children")
    s.add_argument("--raw", help="also write the fetched listings here (survivors with images; without --noun, every listing)")
    common(s)

    f = sub.add_parser("filter", help="filter an existing raw dump (list of listings or a search result)")
    f.add_argument("raw")
    common(f)

    sc = sub.add_parser("score", help="add relevance and market_weight to a full search result from rules.json")
    sc.add_argument("--full", required=True, help="research/etsy.full.json from search; rewritten in place with the scores")
    sc.add_argument("--rules", required=True, help="research/rules.json: noun, required_any, excluded, materials_allowed, materials_excluded")
    sc.add_argument("--out", required=True, help="compact file to rewrite, research/etsy.json")

    t = sub.add_parser("taxonomy", help="look up buyer taxonomy ids")
    t.add_argument("what", choices=["find", "path"])
    t.add_argument("term")
    t.add_argument("--max", type=int, default=40)

    args = ap.parse_args()
    if args.env:
        load_env(args.env)
    # Output directories first, before a key is read or a call is spent.
    for path in (getattr(args, "out", None), getattr(args, "raw", None) if args.cmd == "search" else None):
        ensure_parent(path)
    if getattr(args, "out", None):
        ensure_parent(full_path_of(args.out))

    if args.cmd == "score":
        cmd_score(args)
        return

    args.token_file = args.token_file or default_token_file()
    args.taxonomy_cache = args.taxonomy_cache or default_taxonomy_cache()
    quota = {}
    key = api_key()

    if args.cmd == "taxonomy":
        cmd_taxonomy(args, key, quota)
        return

    bearer = None if args.no_inventory else bearer_token(args.token_file)
    noun = (args.noun or "").strip().lower() or None
    target = target_of(args)

    if args.cmd == "search":
        if args.limit is None:
            args.limit = LIMIT_LADDER if noun else LIMIT_OFFSET
        if not 1 <= args.page_size <= PAGE_MAX:
            die(f"--page-size must be between 1 and {PAGE_MAX}")
        if args.limit < 1 or args.stop_pages < 1:
            die("--limit and --stop-pages must be at least 1")
        if noun:
            parse_ladder(args.ladder_edges)  # refuse a bad ladder before the taxonomy call
        if args.taxonomy_id:
            tax = load_taxonomy(args.taxonomy_cache, key, quota)
            if args.taxonomy_id not in tax:
                die(f"taxonomy_id {args.taxonomy_id} is not in the buyer taxonomy; Etsy would ignore it. "
                    f"Try: etsy_search.py taxonomy find <word>")
        fetch = fetch_ladder if noun else fetch_pages
        survivors, rejected, dropped, pages, stop_reason, total = fetch(
            args.keywords, args, target, noun, key, quota)
        fetched = len(survivors) + len(rejected)
        survivors = enrich(survivors, args.currency, key, quota)
        if not noun:
            # Old behaviour: every listing carries images so a --raw dump can be re-filtered.
            rejected = enrich(rejected, args.currency, key, quota)
        if args.raw:
            json.dump(survivors + rejected, open(args.raw, "w"), indent=1)
        query = args.keywords
    else:
        raw = json.load(open(args.raw))
        listings = [l for l in (raw["results"] if isinstance(raw, dict) else raw) if l]
        survivors, rejected, dropped = prefilter(listings, target, args, noun)
        pages = pages_of(listings, {l["listing_id"] for l in survivors})
        fetched, query, total, stop_reason = len(listings), None, None, None

    result = run_filter(survivors, dropped, args, query, total, quota, key=key, bearer=bearer,
                        fetched=fetched, pages=pages, stop_reason=stop_reason, noun=noun)
    if args.out:
        write_outputs(result, args.out, quota)
    else:
        print(json.dumps(result, indent=1, ensure_ascii=False))
    sys.exit(0 if result["kept"] else 2)


if __name__ == "__main__":
    main()
