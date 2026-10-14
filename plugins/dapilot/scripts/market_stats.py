#!/usr/bin/env python3
"""Market price band and sale margin for one model, from comparables and its quote.

Deterministic. Every number here is arithmetic on files a skill already wrote:
the comparables (comparables.json, chosen by hand or selected here by rules),
3DAPI's quote (quote.json), the pricing policy and the platform fee schedule
(see pricing.py). Nothing is estimated; a missing input is a missing field in
the output, never a default.

    market_stats.py --workdir work/<hash> [--policy FILE] [--fees FILE]
                    [--shipping-usd X] [--packaging-g 30] [--out viability.json]
                    [--rules research/rules.json] [--full research/etsy.full.json]
                    [--summary research/summary.json]

Reads  <workdir>/comparables.json   either shape, see below
       <workdir>/quote.json         quote-model output: unit_price, batch, metrics.model_g
       $DAPILOT_STORE_DIR/reference/pricing-policy.json   cost basis, markup, VAT, platform, region
Writes <workdir>/viability.json (or --out).

Two ways to get comparables:

  Without --rules the script reads an existing comparables.json. Both shapes
  are accepted: the hand-written one ("comparables" with a "reason" per record
  and a "rejected" list) and the "rules" shape written by this script.

  With --rules RULES the script reads the full search result (--full, default
  research/etsy.full.json next to the rules file, else in the work dir),
  scores every kept record, selects those with relevance >= rules.min_relevance
  (default 0.6), writes comparables.json in the "rules" shape and carries on.
  Selected records are ordered by market_weight descending (then relevance,
  then listing_id) and each carries the projected fields plus a "shop" block
  {name, sold, review_count} copied from the full record, so downstream
  scripts can tell one shop from many without opening the full file. The
  compact research/etsy.json beside the full file, when present, gets
  "relevance" and "market_weight" written onto its kept records so the model
  can read scores without opening the full file.

Scores. etsy_search.py owns the formulas (relevance, market_weight, has_word)
and this script imports them when that module provides them, so the two
cannot disagree; a private copy of the same formulas is used otherwise:

  word match  case-insensitive, whole word, plural tolerant (duck/ducks,
              box/boxes, bunny/bunnies); a space in a phrase also matches a
              hyphen; a word may be glued to digits on its left ("10pcs"
              matches "pcs"). Blank rule entries are dropped before use.
  relevance, 0 to 1, rounded to 3 decimals
    0.30  the noun is in the title
    0.20  any required_any word is in the title or tags (an empty list earns
          the credit)
    0.20  no excluded word is in the title or tags; when one is, the record
          carries "excluded_by", the list of words that hit
    0.15  an allowed material is present in materials, title or tags; an
          excluded material present anywhere forces relevance to 0 and is
          listed in "excluded_by" as "material:<word>"
    0.15 x (1 - min(1, |log2(size_ratio)|)) when size_ratio is known, else 0.075
  market_weight, >= 0, rounded to 6 decimals
    the listing's estimated units sold per day, sales_est.units_per_day, as
    the search computed it from the shop's newest reviews (etsy_search.py
    sales_estimates: shop review rate, reviews in the window, add-one share
    over the shop's active listings). A record without an estimate falls
    back to the legacy formula log1p(sold) + 0.5 x log1p(favorers) +
    (1 if sold > 0 else 0), and market.weight_basis says whether the band
    was built on "units_per_day", "legacy" or a "mixed" set.
    market.weight_concentration reports the share of the total weight held
    by the heaviest listing and by the heaviest shop, so a band that is one
    seller's volume is visible without reading the file by hand.

Weighted band. p25, median and p75 over price with market_weight as weights,
by cumulative mass and without interpolation: records sorted by price, the
quantile q is the price of the first record whose cumulative weight reaches
q x total weight, and records tied on price share one step. Records with
zero weight do not move the band. When the total weight is 0 or fewer than 3
records carry weight, the band falls back to unweighted and market.basis says
so. Both blocks are always written; the top-level market p25/median/p75 are
the ones of the basis in use. per_gram is unweighted as before.

Spread. market.spread_ratio is max / min over the selected comparables (null
when min is 0) and market.thin is true when n < 5, or n < 20 and spread_ratio > 3; a
skill reads the flag, it never works it out in prose.

Clones (2026-09-21). Before the band, selected listings from the same shop
at the same price whose titles share most of their words are one
comparable: the first by weight keeps the record, `market_weight` is the
sum (a shop's units are already split over its listings, so the sum gives
the shop's rate back), and `collapsed` on it lists the listing ids folded
in. comparables.json says `n_collapsed`. The coaster run of 2026-09-21 was
four listings of one set by one shop; after this it is one.

Outliers (2026-09-21). With 5 or more priced comparables, Tukey fences on
the log of price: with r = max(p75 / p25, 1.5) over the unweighted
quartiles, a price above p75 x r^1.5 or below p25 / r^1.5 is an outlier.
Outliers stay in comparables.json marked `outlier: "high"|"low"` and are
listed under market.outliers with title, shop and materials, but they do
not enter the band, the spread, the concentration or the demand. A gold
filament version of the same design at 100 USD in a 5 to 15 USD band is
reported to the model, not averaged into the median. market.iqr and
market.iqr_ratio (p75 - p25 and p75 / p25, unweighted, after outliers) are
written for the record.

Demand and concentration (2026-09-21). market.demand sums the sales
estimates of the band: units_per_day over the rows with an estimate, and
how many listings carry any review at all. The verdict is `unreliable`,
before any other reading, when one shop holds CONCENTRATION_MAX (0.6) or
more of the weight, or when the band's total demand is under
DEMAND_MIN_UNITS_PER_DAY (0.1) on a units_per_day basis, or when fewer
than 2 comparables remain after clones are collapsed. market.verdict_reason
says which. price_candidates.py then allows the markup candidate only.

Every path stored in viability.json is relative to the work dir.

--summary FILE writes the compact market summary the research skill shows the
model before it writes rules: counts by material class, a 12-bucket price
histogram, the 30 titles with the highest market_weight and 20 titles sampled
by every k-th record in listing_id order. It works without --rules (the full
file is summarised as is) and stops there; with --rules it adds relevance to
the summary and carries on into comparables.json and viability.json.

Paths given to --rules, --full and --summary are relative to the work dir
unless absolute.

Stock. When the policy sets stock_units, viability.json gains a "stock"
block: plates for that many units, the batch cost at the policy basis, and at
p25, median and p75 the margin per unit, the units that pay the batch back
and the profit if every unit sells.

Verdict, deterministic: `unreliable` as above, else `viable` when the market
median clears the policy markup on cost, `thin` when it is above break-even
but below that, `not_viable` when the median is below break-even. The price
itself is chosen later by propose-price from price_candidates.py; this
script only says what the market allows.

Exit 0 with a verdict, 2 when there are no comparables with a price, 1 on error.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pricing import Model, PricingError, load_fees, load_policy, unit_cost_basis  # noqa: E402

DEFAULT_MIN_RELEVANCE = 0.6
HIST_BUCKETS = 12
TOP_BY_WEIGHT = 30
SAMPLE_TITLES = 20
REJECTED_SAMPLE = 10
CONFIDENCE = ("high", "medium", "low")
CONCENTRATION_MAX = 0.6          # one shop at or above this share of the weight: the band is that shop's price
DEMAND_MIN_UNITS_PER_DAY = 0.1   # under this, on a units_per_day basis, nobody is known to buy
MIN_COMPARABLES = 2              # after clones collapse
OUTLIER_MIN_N = 5                # fewer priced comparables than this: no outlier test
FENCE_POWER = 1.5                # Tukey's 1.5 x IQR, on the log of price
FENCE_MIN_RATIO = 1.5            # a band with p75 == p25 still gets a fence of x1.84 / /1.84
CLONE_TITLE_SIMILARITY = 0.5     # Jaccard on title words, same shop and same price
WITNESSES = ("filename", "drafts", "thumbnails", "analysis")


def identity_of(rules: dict, path) -> dict | None:
    """The research skill's judgement of what the object is, checked for shape and copied as written.

    rules.json may carry
      "identity": {"object": "small desk phone stand", "confidence": "low",
                   "witnesses": ["drafts", "analysis"], "reason": "one sentence"}
    `confidence` is one of high, medium, low; `witnesses` lists which of
    filename, drafts, thumbnails, analysis supported the reading. The block
    is carried into comparables.json and viability.json so the price stage
    and the console can show how sure the identification was. Absent means
    a rules file written before the field existed, and the copies are null.
    """
    ident = rules.get("identity")
    if ident is None:
        return None
    if not isinstance(ident, dict):
        die(f"rules.json identity must be an object: {path}")
    conf = ident.get("confidence")
    if conf not in CONFIDENCE:
        die(f"rules.json identity.confidence must be one of {', '.join(CONFIDENCE)}, not {conf!r}: {path}")
    if not isinstance(ident.get("object"), str) or not ident["object"].strip():
        die(f"rules.json identity.object must be a short phrase: {path}")
    wit = ident.get("witnesses")
    if wit is None:
        wit = []
    if not isinstance(wit, list) or any(w not in WITNESSES for w in wit):
        die(f"rules.json identity.witnesses must list only {', '.join(WITNESSES)}: {path}")
    return {"object": ident["object"].strip(), "confidence": conf, "witnesses": list(wit),
            "reason": ident.get("reason") if isinstance(ident.get("reason"), str) else None}

# Material classes for the summary. The model sees counts per class before it
# writes materials_allowed / materials_excluded, so the vocabulary here is
# what those lists are expected to use.
MATERIAL_CLASSES = {
    "3d_printed": ["pla", "petg", "abs", "tpu", "asa", "3d printed", "3d print", "3d-printed", "filament"],
    "resin": ["resin", "epoxy", "uv resin"],
    "plastic": ["plastic", "acrylic", "vinyl", "silicone", "polymer clay", "nylon"],
    "metal": ["metal", "steel", "stainless", "silver", "gold", "brass", "bronze", "copper", "alloy", "zinc",
              "aluminium", "aluminum", "pewter", "iron", "titanium", "damascus"],
    "wood": ["wood", "wooden", "bamboo", "plywood"],
    "leather": ["leather"],
    "glass": ["glass"],
    "ceramic": ["ceramic", "porcelain", "clay", "stoneware", "terracotta"],
    "stone": ["crystal", "stone", "gemstone", "marble", "quartz"],
    "textile": ["cotton", "wool", "felt", "fabric", "yarn", "polyester", "plush"],
    "paper": ["paper", "cardstock", "cardboard"],
}


def die(msg: str, code: int = 1) -> None:
    print(json.dumps({"ok": False, "error": msg}), file=sys.stderr)
    sys.exit(code)


def load(path: Path, what: str) -> dict:
    if not path.is_file():
        die(f"{what} missing: {path}")
    try:
        return json.loads(path.read_text())
    except ValueError as e:
        die(f"{what} is not JSON: {e}")


def wd_path(wd: Path, p: str | None) -> Path | None:
    if p is None:
        return None
    q = Path(p)
    return q if q.is_absolute() else wd / q


def rel_to(wd: Path, p: Path) -> str:
    """A path as stored in viability.json. Under the work dir: relative to it.
    Under the project root ($DAPILOT_PROJECT_DIR, else the cwd): relative to
    that. Anywhere else: absolute. Never a chain of '..' that only encodes
    this machine's layout."""
    q = Path(p).resolve()
    for base in (wd.resolve(), Path(os.environ.get("DAPILOT_PROJECT_DIR") or os.getcwd()).resolve()):
        try:
            return str(q.relative_to(base))
        except ValueError:
            continue
    return str(q)


# ---------------------------------------------------------------- scoring

_WORD_RE_CACHE: dict[str, re.Pattern] = {}


def _word_re(word: str) -> re.Pattern:
    # Plural tolerant: "duck" matches "ducks", "box" matches "boxes", "bunny"
    # matches "bunnies". A phrase such as "set of" is matched as a whole, and a
    # space in it also matches a hyphen ("3d printed" finds "3D-printed"). The
    # lookbehind excludes letters only, so a word glued to digits on its left
    # still matches ("10pcs" contains "pcs"); the lookahead still refuses
    # letters and digits so "duck" does not match "duck2" or "duckling".
    w = word.strip().lower()
    if w not in _WORD_RE_CACHE:
        esc = re.escape(w).replace(r"\ ", r"[\s\-]+")
        alts = [esc + r"(?:s|es)?"]
        if w.endswith("y") and len(w) > 2:
            alts.append(esc[:-1] + "ies")
        _WORD_RE_CACHE[w] = re.compile(r"(?<![a-z])(?:" + "|".join(alts) + r")(?![a-z0-9])", re.I)
    return _WORD_RE_CACHE[w]


def _has_word(text: str, word: str) -> bool:
    return bool(isinstance(word, str) and word.strip() and _word_re(word).search(text or ""))


def _words(rules: dict, key: str) -> list[str]:
    """A rule list with blank and non-string entries dropped, so "" never matches everything."""
    return [w for w in (rules.get(key) or []) if isinstance(w, str) and w.strip()]


def _is_num(v) -> bool:
    # bool is a subclass of int; True must never count as 1 sale.
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _num(v) -> float:
    return float(v) if _is_num(v) else 0.0


def _sold_of(r: dict):
    # shop.sold when present, else the top-level sold (the compact shape).
    shop = r.get("shop")
    if isinstance(shop, dict) and shop.get("sold") is not None:
        return shop["sold"]
    return r.get("sold")


def _favorers_of(r: dict):
    # num_favorers when present (the full shape), else favorers (the compact one).
    return r["num_favorers"] if r.get("num_favorers") is not None else r.get("favorers")


def _text_of(r: dict) -> tuple[str, str, str]:
    """(title, title plus tags, materials plus title plus tags)."""
    title = r.get("title") or ""
    tags = " ".join(str(t) for t in (r.get("tags") or []))
    mats = " ".join(str(m) for m in (r.get("materials") or []))
    return title, f"{title} {tags}", f"{mats} {title} {tags}"


def _relevance(r: dict, rules: dict) -> tuple[float, list[str]]:
    """The private copy of the formula: (score, excluded_by). The docstring lists the terms."""
    title, title_tags, mat_text = _text_of(r)
    excluded_by = [w for w in _words(rules, "excluded") if _has_word(title_tags, w)]
    banned = [w for w in _words(rules, "materials_excluded") if _has_word(mat_text, w)]
    if banned:
        return 0.0, excluded_by + [f"material:{w}" for w in banned]
    score = 0.0
    if _has_word(title, rules.get("noun")):
        score += 0.30
    required = _words(rules, "required_any")
    if not required or any(_has_word(title_tags, w) for w in required):
        score += 0.20
    if not excluded_by:
        score += 0.20
    if any(_has_word(mat_text, w) for w in _words(rules, "materials_allowed")):
        score += 0.15
    ratio = r.get("size_ratio")
    if _is_num(ratio) and ratio > 0:
        score += 0.15 * (1.0 - min(1.0, abs(math.log2(ratio))))
    else:
        score += 0.075
    return round(min(1.0, score), 3), excluded_by


def _market_weight(r: dict) -> float:
    est = r.get("sales_est") if isinstance(r.get("sales_est"), dict) else None
    upd = est.get("units_per_day") if est else None
    if _is_num(upd):
        return round(max(0.0, float(upd)), 6)
    sold = max(0.0, _num(_sold_of(r)))
    fav = max(0.0, _num(_favorers_of(r)))
    return round(math.log1p(sold) + 0.5 * math.log1p(fav) + (1.0 if sold > 0 else 0.0), 3)


try:  # etsy_search.py owns these once it ships them; until then the copies above apply.
    from etsy_search import has_word, market_weight, relevance  # type: ignore
except ImportError:
    relevance, market_weight, has_word = _relevance, _market_weight, _has_word

try:
    from etsy_search import parse_offer  # type: ignore
except ImportError:  # a record without the search script beside it keeps whatever offer it carries
    def parse_offer(title, description=None, variant_text=None, noun=None):  # type: ignore
        return {"units": 1, "units_source": "default", "text": None, "extras": []}


def weight_of(r: dict) -> float:
    w = r.get("market_weight")
    return float(w) if _is_num(w) and w >= 0 else float(market_weight(r))


# ---------------------------------------------------------------- unit of sale

def units_of(r: dict) -> int | None:
    """How many units one purchase of this listing brings: 1 for a record without an
    offer block (hand-written comparables, files from before 2026-10-12), None when the
    listing says set and no count could be read (it stays out of the per-unit band)."""
    o = r.get("offer")
    if not isinstance(o, dict):
        return 1
    u = o.get("units")
    return u if isinstance(u, int) and not isinstance(u, bool) and u > 0 else None


def unit_price_of(r: dict) -> float | None:
    u = units_of(r)
    p = r.get("price")
    if u is None or not _is_num(p) or p <= 0:
        return None
    return round(float(p) / u, 4)  # six of them add back to the listing price


def offer_of(rows: list[dict], policy: dict) -> dict:
    """The unit of sale the market uses, and so the one we list: the modal pack size by
    weight among the comparables when it holds at least `offer.adopt_share` of the weight
    (policy, default 0.5), else one unit. Numbers only; no prompt chooses this."""
    cfg = policy.get("offer") if isinstance(policy.get("offer"), dict) else {}
    adopt = float(cfg.get("adopt_share", 0.5))
    cap = int(cfg.get("max_units", 24))
    total_w = sum(weight_of(r) for r in rows)
    basis = "weight" if total_w > 0 else "count"
    by: dict[int, dict] = {}
    extras: dict[str, float] = {}
    for r in rows:
        u = units_of(r)
        if u is None:
            continue
        w = weight_of(r) if basis == "weight" else 1.0
        d = by.setdefault(u, {"units": u, "n": 0, "weight": 0.0})
        d["n"] += 1
        d["weight"] += w
        for x in (r.get("offer") or {}).get("extras") or []:
            extras[x] = extras.get(x, 0.0) + w
    total = sum(d["weight"] for d in by.values())
    dist = sorted(({"units": u, "n": d["n"], "share": round(d["weight"] / total, 3) if total else None}
                   for u, d in by.items()), key=lambda x: (-(x["share"] or 0), x["units"]))
    modal = dist[0] if dist else None
    common = sorted(x for x, w in extras.items() if total and w / total >= 0.5)
    if modal and modal["share"] is not None and modal["share"] >= adopt and modal["units"] <= cap:
        units, source = modal["units"], "market_modal"
        note = None if units > 1 else "the market sells singles"
    else:
        units, source = 1, "single"
        note = ("no comparables with a readable unit of sale" if not modal else
                f"the modal pack of {modal['units']} holds {modal['share']:.0%} of the {basis}, under {adopt:.0%}" if modal["share"] is not None and modal["share"] < adopt else
                f"the modal pack of {modal['units']} is over the {cap}-unit cap")
    return {"units": units, "source": source, "note": note,
            "modal_units": modal["units"] if modal else None, "modal_share": modal["share"] if modal else None,
            "share_basis": basis, "adopt_share": adopt, "max_units": cap, "distribution": dist,
            "extras_common": common,
            "rule": "the modal units by market weight when its share is at or above adopt_share and at most max_units; else 1"}


def weight_basis_of(rows: list[dict]) -> tuple[str, dict | None]:
    """Which formula the weights came from, and how concentrated they are.

    "units_per_day" when every row carries a sales estimate, "legacy" when
    none does, "mixed" otherwise (a mixed band is not comparable across rows,
    and the skills should say so). Concentration is the share of the total
    weight held by the heaviest listing and by the heaviest shop; null when
    the rows carry no weight at all.
    """
    def has_est(r):
        est = r.get("sales_est") if isinstance(r.get("sales_est"), dict) else None
        return est is not None and _is_num(est.get("units_per_day"))
    n_est = sum(1 for r in rows if has_est(r))
    basis = "units_per_day" if n_est == len(rows) else "legacy" if n_est == 0 else "mixed"
    total = sum(weight_of(r) for r in rows)
    if total <= 0:
        return basis, None
    by_shop: dict = {}
    for r in rows:
        shop = r.get("shop") if isinstance(r.get("shop"), dict) else {}
        name = shop.get("name")
        by_shop[name] = by_shop.get(name, 0.0) + weight_of(r)
    top_listing = max(rows, key=weight_of)
    top_shop = max(by_shop.items(), key=lambda kv: kv[1])
    return basis, {
        "top_listing_share": round(weight_of(top_listing) / total, 3),
        "top_listing_id": top_listing.get("listing_id"),
        "top_shop_share": round(top_shop[1] / total, 3),
        "top_shop": top_shop[0],
        "rows_with_estimate": n_est,
    }


# ---------------------------------------------------------------- quantiles

def quantiles(xs: list[float]) -> dict:
    xs = sorted(xs)
    if len(xs) == 1:
        q = [xs[0]] * 3
    elif len(xs) < 4:
        q = [xs[0], statistics.median(xs), xs[-1]]
    else:
        q = statistics.quantiles(xs, n=4, method="inclusive")
    return {"n": len(xs), "min": round(xs[0], 2), "p25": round(q[0], 2), "median": round(q[1], 2),
            "p75": round(q[2], 2), "max": round(xs[-1], 2)}


def weighted_quantiles(pairs: list[tuple[float, float]]) -> dict | None:
    """Weighted p25/median/p75 by cumulative mass, no interpolation.

    Records sorted by price; the quantile q is the price of the first record
    whose cumulative weight reaches q x total weight. Records tied on price
    share one step, so the answer is always a price somebody charges: two
    heavy listings at 5 and 6 and a featherweight at 90 give p75 = 6, never
    a number between 6 and 90 that nobody lists at. Records with zero weight
    do not move the band. Returns None when fewer than 3 records carry weight
    or the total weight is 0; the caller then falls back to unweighted.
    """
    rows = [(float(p), float(w)) for p, w in pairs if _is_num(w) and w > 0]
    total = sum(w for _, w in rows)
    if len(rows) < 3 or total <= 0:
        return None
    steps: dict[float, float] = {}
    for p, w in rows:
        steps[p] = steps.get(p, 0.0) + w
    ladder = sorted(steps.items())

    def at(q: float) -> float:
        target = q * total
        cum = 0.0
        for p, w in ladder:
            cum += w
            # A hair of tolerance so 0.75 x total lands on the step it belongs
            # to instead of the next one because of floating point.
            if cum + 1e-9 >= target:
                return p
        return ladder[-1][0]

    return {"n": len(rows), "p25": round(at(0.25), 2), "median": round(at(0.5), 2), "p75": round(at(0.75), 2),
            "weight_total": round(total, 4)}


# ---------------------------------------------------------------- rules -> comparables

def score_records(kept: list[dict], rules: dict) -> list[dict]:
    """Return the kept records with relevance, market_weight and excluded_by set (in place)."""
    for r in kept:
        score, excluded_by = relevance(r, rules)
        r["relevance"] = float(score)
        r["market_weight"] = float(market_weight(r))
        if excluded_by:
            r["excluded_by"] = list(excluded_by)
        else:
            r.pop("excluded_by", None)
        if not isinstance(r.get("offer"), dict):  # a full file searched before the unit of sale existed
            r["offer"] = parse_offer(r.get("title"), r.get("description"), None, rules.get("noun"))
        r["unit_price"] = unit_price_of(r)
    return kept


def comparable_record(r: dict) -> dict:
    shop = r.get("shop") if isinstance(r.get("shop"), dict) else {}
    out = {
        "listing_id": r.get("listing_id"), "url": r.get("url"), "title": r.get("title"),
        "price": r.get("price"), "currency": r.get("currency"), "price_source": r.get("price_source"),
        # What one purchase brings and what one unit of it costs: the band is built on unit_price.
        "offer": r.get("offer") if isinstance(r.get("offer"), dict) else None,
        "unit_price": unit_price_of(r),
        "dims_mm": r.get("dims_mm"), "size_ratio": r.get("size_ratio"), "weight_g": r.get("weight_g"),
        "materials": r.get("materials") or [],
        "sold": _sold_of(r), "reviews": shop.get("review_count", r.get("reviews")),
        "favorers": _favorers_of(r),
        "relevance": r["relevance"], "market_weight": r["market_weight"], "page": r.get("page"),
        # The estimate behind the weight, so a reader can see units per day,
        # the reviews it came from and the window without the full file.
        "sales_est": r.get("sales_est"),
        # Who sells it. price_candidates.py counts distinct shops among the p75
        # sellers from this block, so it is copied rather than left in the full file.
        "shop": {"name": shop.get("name"), "sold": _sold_of(r), "review_count": shop.get("review_count"),
                 "active_listings": shop.get("active_listings")},
    }
    if r.get("excluded_by"):
        out["excluded_by"] = r["excluded_by"]
    if r.get("collapsed"):
        out["collapsed"] = list(r["collapsed"])
    return out


_TITLE_WORD = re.compile(r"[a-z0-9]+")


def _title_words(r: dict) -> frozenset:
    return frozenset(w for w in _TITLE_WORD.findall((r.get("title") or "").lower()) if len(w) > 2)


def collapse_clones(selected: list[dict]) -> list[dict]:
    """One comparable per product a shop lists several times at one price.

    Same shop name, same price to the cent, and titles sharing at least
    CLONE_TITLE_SIMILARITY of their words (Jaccard). The heaviest record
    stays, its market_weight becomes the group's sum, and `collapsed` lists
    the other listing ids. Listings without a shop name never collapse.
    Input is in weight order; output keeps that order.
    """
    out: list[dict] = []
    groups: list[tuple[str, float, frozenset, dict]] = []
    for r in selected:
        shop = r.get("shop") if isinstance(r.get("shop"), dict) else {}
        name, price = shop.get("name"), r.get("price")
        words = _title_words(r)
        if name and _is_num(price) and words:
            for g_name, g_price, g_words, head in groups:
                if g_name == name and abs(g_price - float(price)) < 0.005:
                    j = len(words & g_words) / len(words | g_words)
                    if j >= CLONE_TITLE_SIMILARITY:
                        head["market_weight"] = float(head.get("market_weight") or 0) + float(r.get("market_weight") or 0)
                        head.setdefault("collapsed", []).append(r.get("listing_id"))
                        # Same product, same shop, same price: a clone whose title says "Set of 6" settles the
                        # unit of sale for a head whose title only says "Set".
                        if units_of(head) is None and units_of(r) is not None:
                            head["offer"] = {**r["offer"], "units_source": f"clone:{r.get('listing_id')}"}
                            head["unit_price"] = unit_price_of(head)
                        break
            else:
                groups.append((name, float(price), words, r))
                out.append(r)
        else:
            out.append(r)
    return out


def mark_outliers(rows: list[dict]) -> dict | None:
    """Tukey fences on log price over the unweighted quartiles; sets `outlier` on the rows outside.

    Returns the fences, or None when there are too few rows to judge.
    """
    prices = [float(r["price"]) for r in rows]
    if len(prices) < OUTLIER_MIN_N:
        return None
    q = quantiles(prices)
    if q["p25"] <= 0:
        return None
    ratio = max(q["p75"] / q["p25"], FENCE_MIN_RATIO)
    high = q["p75"] * ratio ** FENCE_POWER
    low = q["p25"] / ratio ** FENCE_POWER
    for r in rows:
        p = float(r["price"])
        if p > high + 1e-9:
            r["outlier"] = "high"
        elif p < low - 1e-9:
            r["outlier"] = "low"
        else:
            r.pop("outlier", None)
    return {"low": round(low, 2), "high": round(high, 2), "p25": q["p25"], "p75": q["p75"],
            "rule": f"outside p25 / r^{FENCE_POWER} .. p75 x r^{FENCE_POWER} with r = max(p75 / p25, {FENCE_MIN_RATIO}), unweighted, n >= {OUTLIER_MIN_N}"}


def demand_of(rows: list[dict]) -> dict:
    """What the band's sales estimates add up to: units a day, and how many listings show any review."""
    ests = [r.get("sales_est") for r in rows if isinstance(r.get("sales_est"), dict)]
    # A collapsed record's market_weight is the sum over its clones, and on a
    # units_per_day basis the weight is the estimate, so the weight is the demand.
    upd = [float(r["market_weight"]) if _is_num(r.get("market_weight")) else float(r["sales_est"]["units_per_day"])
           for r in rows if isinstance(r.get("sales_est"), dict) and _is_num(r["sales_est"].get("units_per_day"))]
    reviewed = sum(1 for e in ests if _is_num(e.get("listing_reviews")) and e["listing_reviews"] > 0)
    return {"units_per_day": round(sum(upd), 4) if upd else None, "listings_with_estimate": len(upd),
            "listings_with_reviews": reviewed, "n": len(rows)}


def build_comparables(full: dict, rules: dict, min_rel: float) -> dict:
    kept = score_records(list(full.get("kept") or []), rules)
    # Heaviest sellers first: the review step reads the top 20 by market
    # weight, and the order in the file is that order.
    selected = sorted((r for r in kept if r["relevance"] >= min_rel),
                      key=lambda r: (-r["market_weight"], -r["relevance"], r.get("listing_id") or 0))
    n_before = len(selected)
    selected = collapse_clones(selected)
    selected.sort(key=lambda r: (-r["market_weight"], -r["relevance"], r.get("listing_id") or 0))
    # The near misses are the useful rejects: they show the model what a
    # tighter or looser rule would change.
    rejected = sorted((r for r in kept if r["relevance"] < min_rel),
                      key=lambda r: (-r["relevance"], r.get("listing_id") or 0))
    return {
        "mode": "rules",
        "product": rules.get("product") or full.get("product") or rules.get("noun"),
        "identity": identity_of(rules, "rules.json"),
        "queries": [full["query"]] if full.get("query") else [],
        "taxonomy_id": rules.get("taxonomy_id", full.get("taxonomy_id")),
        "currency": full.get("currency") or "USD",
        "rules": rules,
        "n_scored": len(kept),
        "n_selected": len(selected),
        "n_collapsed": n_before - len(selected),
        "min_relevance": min_rel,
        "comparables": [comparable_record(r) for r in selected],
        "rejected_count": len(rejected),
        "rejected_sample": [comparable_record(r) for r in rejected[:REJECTED_SAMPLE]],
    }


def annotate_compact(compact_path: Path, kept: list[dict]) -> bool:
    """Write relevance and market_weight onto the compact etsy.json's kept records."""
    if not compact_path.is_file():
        return False
    try:
        compact = json.loads(compact_path.read_text())
    except ValueError:
        return False
    rows = compact.get("kept")
    if not isinstance(rows, list) or not all(isinstance(r, dict) and "listing_id" in r for r in rows):
        return False
    by_id = {r["listing_id"]: r for r in kept}
    for r in rows:
        src = by_id.get(r["listing_id"])
        if src is None:
            continue
        r["relevance"], r["market_weight"] = src["relevance"], src["market_weight"]
        if src.get("excluded_by"):
            r["excluded_by"] = src["excluded_by"]
        else:
            r.pop("excluded_by", None)
    compact_path.write_text(json.dumps(compact, indent=1, ensure_ascii=False))
    return True


# ---------------------------------------------------------------- summary

def material_classes_of(r: dict) -> list[str]:
    _, _, mat_text = _text_of(r)
    found = [cls for cls, words in MATERIAL_CLASSES.items() if any(has_word(mat_text, w) for w in words)]
    return found or ["unstated"]


def histogram(prices: list[float], buckets: int = HIST_BUCKETS) -> list[dict]:
    if not prices:
        return []
    lo, hi = min(prices), max(prices)
    if hi <= lo:
        return [{"lo": round(lo, 2), "hi": round(hi, 2), "n": len(prices)}]
    width = (hi - lo) / buckets
    counts = [0] * buckets
    for p in prices:
        counts[min(buckets - 1, int((p - lo) / width))] += 1
    return [{"lo": round(lo + i * width, 2), "hi": round(lo + (i + 1) * width, 2), "n": counts[i]}
            for i in range(buckets)]


def title_row(r: dict, scored: bool) -> dict:
    row = {"listing_id": r.get("listing_id"), "title": r.get("title"), "price": r.get("price"),
           "units": units_of(r), "unit_price": unit_price_of(r),
           "sold": _sold_of(r), "favorers": _favorers_of(r), "materials": (r.get("materials") or [])[:4],
           "size_ratio": r.get("size_ratio"), "market_weight": round(weight_of(r), 6)}
    est = r.get("sales_est") if isinstance(r.get("sales_est"), dict) else None
    if est:
        row["units_per_day"] = est.get("units_per_day")
        row["reviews_in_window"] = est.get("listing_reviews")
    if scored:
        row["relevance"] = r.get("relevance")
        if r.get("excluded_by"):
            row["excluded_by"] = r["excluded_by"]
    return row


def build_summary(full: dict, rules: dict | None) -> dict:
    kept = list(full.get("kept") or [])
    scored = rules is not None
    if scored:
        score_records(kept, rules)
    for r in kept:  # a full file from before the unit of sale existed
        if not isinstance(r.get("offer"), dict):
            r["offer"] = parse_offer(r.get("title"), r.get("description"), None, full.get("noun") or (rules or {}).get("noun"))
    prices = [float(r["price"]) for r in kept if _is_num(r.get("price")) and r["price"] > 0]
    classes: dict[str, int] = {}
    raw_mats: dict[str, int] = {}
    for r in kept:
        for c in material_classes_of(r):
            classes[c] = classes.get(c, 0) + 1
        for m in r.get("materials") or []:
            m = str(m).strip().lower()
            if m:
                raw_mats[m] = raw_mats.get(m, 0) + 1
    # Once rules exist the review reads the selected comparables, so the top
    # list is drawn from them and the heaviest rejects are shown apart; before
    # rules, every kept record is a candidate and the list covers them all.
    min_rel = float(rules.get("min_relevance", DEFAULT_MIN_RELEVANCE)) if scored else None
    pool = [r for r in kept if r["relevance"] >= min_rel] if scored else kept
    rejects = [r for r in kept if r["relevance"] < min_rel] if scored else []
    by_weight = sorted(pool, key=lambda r: (-weight_of(r), r.get("listing_id") or 0))
    rejects_by_weight = sorted(rejects, key=lambda r: (-weight_of(r), r.get("listing_id") or 0))
    by_id = sorted(kept, key=lambda r: r.get("listing_id") or 0)
    k = max(1, len(by_id) // SAMPLE_TITLES)
    sample = by_id[::k][:SAMPLE_TITLES]
    out = {
        "ok": True,
        "query": full.get("query"),
        "taxonomy_id": full.get("taxonomy_id"),
        "currency": full.get("currency"),
        "target_mm": full.get("target_mm"),
        "n_fetched": full.get("fetched"),
        "n_kept": len(kept),
        "n_dropped": len(full["dropped"]) if isinstance(full.get("dropped"), list) else full.get("dropped"),
        "pages": full.get("pages"),
        "stop_reason": full.get("stop_reason"),
        "material_classes": dict(sorted(classes.items(), key=lambda kv: (-kv[1], kv[0]))),
        "materials_top": dict(sorted(raw_mats.items(), key=lambda kv: (-kv[1], kv[0]))[:20]),
        "price": quantiles(prices) if prices else None,
        "price_histogram": histogram(prices),
        # How the aisle sells: listings per unit of sale ("6" is a set of six; "unknown" said set with no count).
        "units_distribution": dict(sorted(((str(units_of(r) or "unknown"), sum(1 for x in kept if units_of(x) == units_of(r)))
                                           for r in kept), key=lambda kv: -kv[1])),
        "unit_price": quantiles([up for up in (unit_price_of(r) for r in kept) if up]) if any(unit_price_of(r) for r in kept) else None,
        "with_size_ratio": sum(1 for r in kept if _is_num(r.get("size_ratio"))),
        "with_weight_g": sum(1 for r in kept if _is_num(r.get("weight_g"))),
        "top_by_market_weight": [{**title_row(r, scored), **({"selected": True} if scored else {})} for r in by_weight[:TOP_BY_WEIGHT]],
        "sample_every_kth": {"k": k, "titles": [title_row(r, scored) for r in sample]},
    }
    if scored:
        out["rules"] = rules
        out["min_relevance"] = min_rel
        out["n_selected"] = len(pool)
        out["top_rejected_by_market_weight"] = [{**title_row(r, True), "selected": False} for r in rejects_by_weight[:REJECTED_SAMPLE]]
        out["relevance_histogram"] = histogram([r["relevance"] for r in kept], 10)
    return out


# ---------------------------------------------------------------- shipping

def shipping_from_table(table, parcel_g):
    for row in sorted(table, key=lambda r: r["max_g"]):
        if parcel_g <= row["max_g"]:
            return row["usd"]
    return None


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--policy", help="pricing policy JSON; default $DAPILOT_STORE_DIR/reference/pricing-policy.json")
    ap.add_argument("--fees", help="platform fee schedule JSON; default store copy, else the plugin copy")
    ap.add_argument("--cost-basis", choices=["one_off", "full_plate"], help="override the policy's cost basis for this run")
    ap.add_argument("--shipping-usd", type=float, help="shipping cost per unit; overrides the fee file's table")
    ap.add_argument("--packaging-g", type=float, default=None)
    ap.add_argument("--out", help="where to write viability.json (default <workdir>/viability.json)")
    ap.add_argument("--rules", help="rules.json written by the research skill; scores the full search result "
                                    "and writes <workdir>/comparables.json in the rules shape before the band")
    ap.add_argument("--full", help="full search result to score; default research/etsy.full.json beside --rules, "
                                   "else <workdir>/research/etsy.full.json")
    ap.add_argument("--summary", help="also write the compact market summary here (works without --rules)")
    args = ap.parse_args()

    wd = Path(args.workdir)
    if not wd.is_dir():
        die(f"workdir missing: {wd}")

    rules = None
    full = None
    full_path = wd_path(wd, args.full)
    if args.rules:
        rules_path = wd_path(wd, args.rules)
        rules = load(rules_path, "rules.json")
        if not isinstance(rules, dict) or not rules.get("noun"):
            die(f"rules.json needs at least a noun: {rules_path}")
        identity_of(rules, rules_path)
        if full_path is None:
            cand = rules_path.parent / "etsy.full.json"
            full_path = cand if cand.is_file() else wd / "research" / "etsy.full.json"
    elif args.summary and full_path is None:
        full_path = wd / "research" / "etsy.full.json"
    if full_path is not None:
        full = load(full_path, "full search result")
        if not isinstance(full.get("kept"), list):
            die(f"full search result has no kept list: {full_path}")

    summary_path = wd_path(wd, args.summary)
    if summary_path is not None:
        summary = build_summary(json.loads(json.dumps(full)), rules)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=1, ensure_ascii=False))
        if rules is None:
            print(json.dumps({"ok": True, "summary": str(summary_path), "n_kept": summary["n_kept"],
                              "note": "no --rules; stopped after the summary"}))
            return 0

    annotated = None
    if rules is not None:
        min_rel = float(rules.get("min_relevance", DEFAULT_MIN_RELEVANCE))
        comps = build_comparables(full, rules, min_rel)
        (wd / "comparables.json").write_text(json.dumps(comps, indent=2, ensure_ascii=False))
        annotated = annotate_compact(full_path.parent / "etsy.json", list(full["kept"]))
    else:
        comps = load(wd / "comparables.json", "comparables.json")
    quote = load(wd / "quote.json", "quote.json")
    try:
        policy, policy_path = load_policy(args.policy)
        fees, fees_path = load_fees(policy, args.fees)
        model = Model(policy, fees)
    except PricingError as e:
        die(str(e))
    fee_doc = json.loads(fees_path.read_text())
    ship_cfg = fee_doc.get("shipping", {})

    # ---- market side
    currency = comps.get("currency") or policy.get("currency", "USD")
    rows = [c for c in comps.get("comparables", []) if _is_num(c.get("price")) and c["price"] > 0]
    foreign = [c for c in rows if (c.get("currency") or currency) != currency]
    if foreign:
        die(f"{len(foreign)} comparables priced in a currency other than {currency}; search with --currency so Etsy converts them")
    dst = Path(args.out) if args.out else wd / "viability.json"
    # The band is per unit of sale. A listing that says "set" with no readable
    # count has no unit price and stays out, reported under market.unknown_units.
    unknown = [c for c in rows if units_of(c) is None]
    rows = [c for c in rows if units_of(c) is not None]
    if not rows:
        out = {"ok": False, "n_comparables": 0, "mode": comps.get("mode", "hand"),
               "reason": "no comparables with a price" if not unknown else
               f"{len(unknown)} comparable(s) priced as a set with no readable count, none per unit"}
        if rules is not None:
            out["n_scored"], out["min_relevance"] = comps["n_scored"], comps["min_relevance"]
        dst.write_text(json.dumps(out, indent=2))
        print(json.dumps(out))
        return 2
    offer = offer_of(rows, policy)
    # From here every `price` is a unit price; what the buyer pays travels as listing_price.
    rows = [{**c, "price": unit_price_of(c), "listing_price": c["price"]} for c in rows]
    all_rows = rows
    fences = mark_outliers(rows)
    outliers = [c for c in rows if c.get("outlier")]
    rows = [c for c in rows if not c.get("outlier")]
    if rules is not None and outliers:
        # comparables.json keeps them, marked, so the review step and price_candidates.py see the same rows.
        by_id = {c.get("listing_id"): c for c in comps["comparables"]}
        for c in outliers:
            if c.get("listing_id") in by_id:
                by_id[c["listing_id"]]["outlier"] = c["outlier"]
        (wd / "comparables.json").write_text(json.dumps(comps, indent=2, ensure_ascii=False))
    unweighted = quantiles([float(c["price"]) for c in rows])
    weighted = weighted_quantiles([(float(c["price"]), weight_of(c)) for c in rows])
    basis = "weighted" if weighted else "unweighted"
    market = dict(unweighted)
    if weighted:
        market.update({k: weighted[k] for k in ("p25", "median", "p75")})
    market["basis"] = basis
    market["weighted"] = weighted
    market["unweighted"] = unweighted
    market["currency"] = currency
    # Spread over the selected comparables, whatever the basis: a band that
    # runs 3x from cheapest to dearest, or one built on fewer than 5
    # listings, is thin and the skills read the flag rather than judging it.
    market["spread_ratio"] = round(unweighted["max"] / unweighted["min"], 3) if unweighted["min"] > 0 else None
    # Thin means the median is weak evidence: too few listings, or so few that
    # a wide spread is a handful of outliers. A big aisle spans 20x from junk
    # to premium and its median is still solid, so the spread test only
    # applies under 20 listings.
    market["thin"] = bool(unweighted["n"] < 5 or (unweighted["n"] < 20 and market["spread_ratio"] is not None and market["spread_ratio"] > 3))
    market["weight_basis"], market["weight_concentration"] = weight_basis_of(rows)
    market["iqr"] = round(unweighted["p75"] - unweighted["p25"], 2)
    market["iqr_ratio"] = round(unweighted["p75"] / unweighted["p25"], 3) if unweighted["p25"] > 0 else None
    market["outlier_fences"] = fences
    market["outliers"] = [{"listing_id": c.get("listing_id"), "price": c.get("listing_price"), "unit_price": c.get("price"),
                           "units": units_of(c), "side": c["outlier"],
                           "title": c.get("title"), "shop": (c.get("shop") or {}).get("name"),
                           "materials": c.get("materials") or [], "url": c.get("url")} for c in outliers]
    market["n_before_outliers"] = len(all_rows)
    market["n_collapsed"] = comps.get("n_collapsed", 0)
    market["demand"] = demand_of(rows)
    # The listing's stated weight is the whole parcel, so per gram is listing price over it.
    weighed = [c for c in rows if _is_num(c.get("weight_g")) and c["weight_g"] > 0]
    market["per_gram"] = quantiles([c["listing_price"] / c["weight_g"] for c in weighed]) if weighed else None
    market["listings_with_weight"] = len(weighed)
    # Since 2026-10-12 the band above is per unit of sale (the kumiko coaster read a 31 USD
    # set of six as a 31 USD coaster). What buyers pay at checkout is kept beside it.
    market["price_basis"] = "per_unit"
    lw = weighted_quantiles([(float(c["listing_price"]), weight_of(c)) for c in rows])
    market["per_listing"] = {**quantiles([float(c["listing_price"]) for c in rows]),
                             **({k: lw[k] for k in ("p25", "median", "p75")} if lw else {}),
                             "basis": "weighted" if lw else "unweighted"}
    market["unknown_units"] = [{"listing_id": c.get("listing_id"), "title": c.get("title"), "price": c.get("price"),
                                "text": (c.get("offer") or {}).get("text"), "url": c.get("url")} for c in unknown]
    # The band at the unit of sale, from the unrounded unit prices so six of them add back to the set price.
    ow = weighted_quantiles([(float(c["price"]) * offer["units"], weight_of(c)) for c in rows])
    offer["market"] = {**quantiles([float(c["price"]) * offer["units"] for c in rows]),
                       **({k: ow[k] for k in ("p25", "median", "p75")} if ow else {}), "basis": "weighted" if ow else "unweighted"}

    # ---- our side
    metrics = quote.get("metrics") or {}
    model_g = metrics.get("model_g")
    try:
        cb = unit_cost_basis(quote, policy, args.cost_basis)
    except PricingError as e:
        die(str(e))
    unit_1, unit_plate, max_per_plate = cb["one_off"], cb["full_plate"], cb["max_per_plate"]

    packaging_g = args.packaging_g if args.packaging_g is not None else ship_cfg.get("packaging_g", 0)
    parcel_g = round(model_g + packaging_g, 1) if isinstance(model_g, (int, float)) else None
    if args.shipping_usd is not None:
        shipping, shipping_source = args.shipping_usd, "--shipping-usd"
    elif ship_cfg.get("table_usd") and parcel_g is not None:
        shipping = shipping_from_table(ship_cfg["table_usd"], parcel_g)
        shipping_source = f"{fees_path.name} table" if shipping is not None else "parcel heavier than the table"
    else:
        shipping, shipping_source = None, "unknown: no --shipping-usd and no table in the fee file"

    # The policy's basis comes first: the verdict and price_candidates.py read
    # scenarios[0]. The other basis stays in the file as the road not taken.
    order = [(unit_1, "one_off"), (unit_plate, "full_plate")]
    if cb["basis"] == "full_plate":
        order.reverse()
    scenarios = []
    for q, tag in order:
        if tag == "full_plate" and max_per_plate <= 1:
            continue
        cost = model.cost(q)
        units = offer["units"]
        pack_cost = cost * units
        try:
            be = model.break_even(cost)
            be_pack = model.break_even(pack_cost)
        except PricingError as e:
            die(str(e))
        scenarios.append({
            "cost_basis": tag,
            "is_policy_basis": tag == cb["basis"] or (cb["basis"] == "full_plate" and max_per_plate <= 1),
            "quote_unit_price": round(q, 2),
            "unit_cost": round(cost, 2),
            "units_per_plate": 1 if tag == "one_off" else max_per_plate,
            "break_even_price": round(be, 2),
            "markup_price": round(model.markup_price(cost), 2),
            "at": [model.point(k, market[k], cost) for k in ("p25", "median", "p75")],
            # The same at the unit of sale: a pack costs units x unit cost, the fixed Etsy
            # fees are paid once per sale, so its break-even is solved on the pack, not
            # multiplied up. The verdict and price_candidates.py read this block.
            "offer": {
                "units": units,
                "cost": round(pack_cost, 2),
                "break_even_price": round(be_pack, 2),
                "markup_price": round(model.markup_price(pack_cost), 2),
                "at": [model.point(k, offer["market"][k], pack_cost, per_unit=round(market[k], 2), units=units)
                       for k in ("p25", "median", "p75")],
            },
        })

    # Stock economics: what a production run costs and how many sales pay it
    # back at each band price. Plates are whole, and the last plate's unit
    # price applies to every unit (3DAPI's curve is per plate count).
    stock = None
    units = int(policy.get("stock_units") or 0)
    if units > 0:
        plates = -(-units // max_per_plate)
        batch_cost = round(model.cost(unit_plate if max_per_plate > 1 else unit_1) * units, 2)
        cost_used = model.cost(unit_plate if max_per_plate > 1 else unit_1)
        payback = []
        for k in ("p25", "median", "p75"):
            m_unit = model.margin(market[k], cost_used)
            payback.append({"label": k, "price": market[k], "margin_per_unit": round(m_unit, 2),
                            "units_to_pay_back": -(-batch_cost // m_unit) if m_unit > 0 else None,
                            "profit_if_all_sold": round(m_unit * units, 2)})
        stock = {"units": units, "plates": plates, "unit_cost": round(cost_used, 2), "batch_cost": batch_cost,
                 "payback": payback}

    s0 = scenarios[0]
    med = market["median"]
    conc = market.get("weight_concentration") or {}
    demand = market["demand"]
    unreliable = []
    if unweighted["n"] < MIN_COMPARABLES:
        unreliable.append(f"{unweighted['n']} comparable(s) after clones collapsed")
    if _is_num(conc.get("top_shop_share")) and conc["top_shop_share"] >= CONCENTRATION_MAX:
        unreliable.append(f"{conc.get('top_shop') or 'one shop'} holds {conc['top_shop_share']:.0%} of the weight")
    if market["weight_basis"] == "units_per_day" and _is_num(demand.get("units_per_day")) \
            and demand["units_per_day"] < DEMAND_MIN_UNITS_PER_DAY:
        unreliable.append(f"total demand {demand['units_per_day']:.3f} units a day, "
                          f"{demand['listings_with_reviews']} of {demand['n']} listings with any review")
    # Judged at the unit of sale: the market's pack price against the pack's break-even.
    med_offer = offer["market"]["median"]
    if unreliable:
        verdict = "unreliable"
    elif med_offer < s0["offer"]["break_even_price"]:
        verdict = "not_viable"
    elif med_offer < s0["offer"]["markup_price"]:
        verdict = "thin"
    else:
        verdict = "viable"
    market["verdict_reason"] = "; ".join(unreliable) if unreliable else None

    out = {
        "ok": True,
        "product": comps.get("product"),
        "n_comparables": len(rows),
        "identity": comps.get("identity"),
        "comparables_mode": comps.get("mode", "hand"),
        "market": market,
        # The unit of sale we list, decided from the comparables (offer_of). Everything
        # downstream that sells, designs, writes or orders reads offer.units.
        "offer": offer,
        "cost_basis": cb["basis"],
        "ours": {
            "quote_unit_price_one_off": round(unit_1, 2),
            "quote_unit_price_full_plate": round(unit_plate, 2),
            "plate_discount_pct": cb["plate_discount_pct"],
            "max_per_plate": max_per_plate,
            "model_g": model_g,
            "parcel_g": parcel_g,
            "per_gram_at_market_median": round(med / model_g, 3) if model_g else None,
            "shipping_usd": shipping,
            "shipping_source": shipping_source,
            "shipping_absorbed_by_seller": bool(ship_cfg.get("seller_pays", False)),
        },
        "policy": {"file": rel_to(wd, policy_path), **model.describe()},
        "fees_file": rel_to(wd, fees_path),
        "scenarios": scenarios,
        "stock": stock,
        "verdict": verdict,
        "verdict_reason": market["verdict_reason"],
        "verdict_rule": "unreliable when one shop holds 0.6 of the weight, demand is under 0.1 units a day, or fewer than 2 comparables remain; "
                        "else, at the unit of sale (offer.units), market median vs break-even (not_viable below) and vs cost plus markup (thin below, viable at or above)",
    }
    if rules is not None:
        out["selection"] = {"rules": rel_to(wd, wd_path(wd, args.rules)), "full": rel_to(wd, full_path),
                            "n_scored": comps["n_scored"], "n_selected": comps["n_selected"],
                            "min_relevance": comps["min_relevance"], "compact_annotated": bool(annotated)}
    dst.write_text(json.dumps(out, indent=2))
    line = {"ok": True, "out": str(dst), "verdict": verdict, "verdict_reason": market["verdict_reason"],
            "n": len(rows), "n_collapsed": market["n_collapsed"], "outliers": len(outliers),
            "demand_units_per_day": demand.get("units_per_day"), "top_shop_share": conc.get("top_shop_share"),
            "basis": basis, "median": med, "price_basis": "per_unit",
            "offer_units": offer["units"], "offer_source": offer["source"], "offer_median": round(med_offer, 2),
            "unknown_units": len(unknown),
            "unweighted_median": unweighted["median"], "unit_cost": s0["unit_cost"], "offer_cost": s0["offer"]["cost"],
            "break_even": s0["offer"]["break_even_price"], "markup_price": s0["offer"]["markup_price"],
            "margin_pct_at_median": s0["offer"]["at"][1]["margin_pct"]}
    if rules is not None:
        line.update({"n_scored": comps["n_scored"], "n_selected": comps["n_selected"]})
    if summary_path is not None:
        line["summary"] = str(summary_path)
    print(json.dumps(line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
