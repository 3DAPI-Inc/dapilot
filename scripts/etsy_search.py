#!/usr/bin/env python
"""Search Etsy for comparable listings and keep only tangible, physical products.

Deterministic. No judgement: this script drops what the data proves is not a
physical object (digital downloads, file bundles, supplies) and what is the
wrong size for the model being priced. Whether a surviving listing is really
the same kind of object is the model's call, downstream.

Key-only calls need ETSY_KEYSTRING and ETSY_SHARED_SECRET (env or --env).
With an OAuth token file (scripts/etsy_auth.py login) the script also reads
each kept listing's inventory and picks the variant closest to the target size.

    scripts/etsy_search.py search "articulated shark" --taxonomy-id 1580 \
        --analysis work/<hash>/analysis.json --env store/.env --out comparables.json
    scripts/etsy_search.py filter raw.json --size-mm 120,60,40
    scripts/etsy_search.py taxonomy find "desk toy"        # buyer taxonomy ids for --taxonomy-id
    scripts/etsy_search.py taxonomy path 1793

Search only honours ids from the *buyer* taxonomy; a parent id includes its
children; an unknown id is silently ignored by Etsy, so this script refuses it.

Output JSON: {query, currency, fetched, kept: [...], dropped: [{listing_id, url, reason}], quota}
Exit 0 with kept results, 2 when nothing survived, 1 on error.
"""
import argparse
import json
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
DEFAULT_TOKEN_FILE = "store/etsy_token.json"
DEFAULT_TAXONOMY_CACHE = "store/reference/etsy_buyer_taxonomy.json"
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


def get(path, params, key, quota, bearer=None):
    url = f"{BASE}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    headers = {"x-api-key": key}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                quota["remaining_today"] = r.headers.get("x-remaining-today")
                quota["limit_per_day"] = r.headers.get("x-limit-per-day")
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(int(e.headers.get("retry-after", "2")))
                continue
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


# ---------------------------------------------------------------- search + enrich

def search(keywords, limit, currency, taxonomy_id, key, quota):
    results = []
    offset = 0
    while len(results) < limit:
        params = {"keywords": keywords, "limit": min(100, limit - len(results)), "offset": offset,
                  "sort_on": "score", "currency": currency}
        if taxonomy_id:
            params["taxonomy_id"] = taxonomy_id
        page = get("/listings/active", params, key, quota)
        results.extend(page["results"])
        offset += len(page["results"])
        if not page["results"] or offset >= page["count"]:
            break
    return results, page["count"] if results else 0


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
    return [by_id.get(i) or next(l for l in listings if l["listing_id"] == i) for i in ids]


# ---------------------------------------------------------------- units

def price_of(l):
    p = l.get("converted_price") or l.get("price")
    if not p:
        return None, None
    return round(p["amount"] / p["divisor"], 2), p["currency_code"]


def dims_mm(l):
    if not l.get("item_length") or not l.get("item_dimensions_unit"):
        return None
    f = UNIT_MM.get(l["item_dimensions_unit"])
    if not f:
        return None
    return sorted([round(float(l[k]) * f, 1) for k in ("item_length", "item_width", "item_height")], reverse=True)


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
    if not os.path.exists(token_file):
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
    }


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


def run_filter(listings, args, query, total, quota, key=None, bearer=None):
    target = None
    if args.analysis:
        target = sorted(json.load(open(args.analysis))["bbox_mm"], reverse=True)
    elif args.size_mm:
        target = sorted((float(x) for x in args.size_mm.split(",")), reverse=True)
    kept, dropped = [], []
    for l in listings:
        why = reject_reason(l, target, args.size_min_ratio, args.size_max_ratio)
        if why:
            dropped.append({"listing_id": l["listing_id"], "url": l.get("url"), "reason": why})
            continue
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
        "taxonomy_id": getattr(args, "taxonomy_id", None),
        "currency": args.currency,
        "target_mm": target,
        "size_ratio_window": [args.size_min_ratio, args.size_max_ratio] if target else None,
        "total_matches": total,
        "fetched": len(listings),
        "inventory_read": bool(bearer),
        "kept": kept,
        "dropped": dropped,
        "quota": quota,
    }


# ---------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", help="path to a .env file to read the Etsy key from")
    ap.add_argument("--token-file", default=DEFAULT_TOKEN_FILE, help="OAuth token from etsy_auth.py; enables the inventory pass")
    ap.add_argument("--no-inventory", action="store_true", help="skip the per-listing inventory calls even if a token exists")
    ap.add_argument("--taxonomy-cache", default=DEFAULT_TAXONOMY_CACHE)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--analysis", help="work/<hash>/analysis.json; its bbox_mm sets the size window")
        p.add_argument("--size-mm", help="target size as L,W,H in mm, if no analysis.json")
        p.add_argument("--size-min-ratio", type=float, default=0.5, help="drop if longest edge / target < this")
        p.add_argument("--size-max-ratio", type=float, default=2.0, help="drop if longest edge / target > this")
        p.add_argument("--currency", default="USD")
        p.add_argument("--desc-chars", type=int, default=600)
        p.add_argument("--out", help="write the result here instead of stdout")

    s = sub.add_parser("search", help="query Etsy, enrich, filter")
    s.add_argument("keywords")
    s.add_argument("--limit", type=int, default=25, help="listings to fetch before filtering")
    s.add_argument("--taxonomy-id", type=int, help="buyer taxonomy id; parents include children")
    s.add_argument("--raw", help="also write the unfiltered listings with images here")
    common(s)

    f = sub.add_parser("filter", help="filter an existing raw dump (list of listings or a search result)")
    f.add_argument("raw")
    common(f)

    t = sub.add_parser("taxonomy", help="look up buyer taxonomy ids")
    t.add_argument("what", choices=["find", "path"])
    t.add_argument("term")
    t.add_argument("--max", type=int, default=40)

    args = ap.parse_args()
    if args.env:
        load_env(args.env)
    quota = {}
    key = api_key()

    if args.cmd == "taxonomy":
        cmd_taxonomy(args, key, quota)
        return

    bearer = None if args.no_inventory else bearer_token(args.token_file)

    if args.cmd == "search":
        if args.taxonomy_id:
            tax = load_taxonomy(args.taxonomy_cache, key, quota)
            if args.taxonomy_id not in tax:
                die(f"taxonomy_id {args.taxonomy_id} is not in the buyer taxonomy; Etsy would ignore it. "
                    f"Try: etsy_search.py taxonomy find <word>")
        listings, total = search(args.keywords, args.limit, args.currency, args.taxonomy_id, key, quota)
        listings = enrich(listings, args.currency, key, quota)
        if args.raw:
            json.dump(listings, open(args.raw, "w"), indent=1)
        query = args.keywords
    else:
        raw = json.load(open(args.raw))
        listings = [l for l in (raw["results"] if isinstance(raw, dict) else raw) if l]
        query, total = None, None

    result = run_filter(listings, args, query, total, quota, key=key, bearer=bearer)
    text = json.dumps(result, indent=1, ensure_ascii=False)
    if args.out:
        open(args.out, "w").write(text)
        print(json.dumps({"ok": True, "kept": len(result["kept"]), "dropped": len(result["dropped"]),
                          "inventory_read": result["inventory_read"], "out": args.out, "quota": quota}))
    else:
        print(text)
    sys.exit(0 if result["kept"] else 2)


if __name__ == "__main__":
    main()
