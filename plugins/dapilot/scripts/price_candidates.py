#!/usr/bin/env python3
"""The sale-price candidates for one model, and the check that a chosen price obeys the rules.

Deterministic. The model (propose-price) picks ONE of the candidates written
here and never types a number of its own; this script is also what verifies
that afterwards. Policy from $DAPILOT_STORE_DIR/reference/pricing-policy.json,
fees from the platform fee schedule, formula in pricing.py.

    price_candidates.py --workdir work/<hash> [--out price.candidates.json]
    price_candidates.py --workdir work/<hash> --check [price.json]

Candidates, per the policy decided 2026-09-09 and the deep market read of
2026-09-10:
  markup   quote unit price plus markup_pct (50%), rounded UP to the policy ending
  median   the market median from viability.json, rounded to the nearest ending
  p75      the market p75 from viability.json, rounded to the nearest ending.
           When viability.json says market.basis is "weighted" this is the
           weighted p75 and the candidate's `how` says so.
A candidate whose input is missing (no market block, no median, no p75) is
listed under `skipped` with the reason, never raised. p75 is also skipped when
its rounded price equals the rounded price of ANY other candidate (markup or
median); the reason names which. Each emitted candidate carries net, fees,
margin and margin percent. A candidate below the hard floor, or under the
policy's min_margin_pct, is listed under `rejected` with the reason, never
under `candidates`.

`p75_sellers` is the evidence for test (c) of the p75 positioning rule, "the
p75 is not one shop's price". It is computed over the selected comparables in
comparables.json priced at or above market.p75 (the whole selection, not a
top-N), grouped by shop:
  shops                [{name, weight_share}] ordered by share, name null for
                       listings without a shop name; weight_share is the
                       shop's share of market_weight within that set (share
                       of listing count when the set carries no weight at all)
  distinct_named_shops how many named shops hold a price at or above p75
  top_share            the largest weight_share among the named shops
  unnamed_share        the share held by listings with no shop name
The skill reads three numbers and nothing else: (c) holds when
distinct_named_shops >= 2, top_share < 0.6 and unnamed_share < 0.2. Shop
names come from each record's shop.name; a record without one is looked up
by listing_id in research/etsy.full.json, and a listing named in neither
counts as unnamed. Null when market.p75 is absent or comparables.json is
missing or unreadable; `p75_sellers_note` says why.

The hard floor is break-even: cost plus platform fees plus VAT owed, rounded
UP to the policy ending. It applies to every author of price.json, model or
person. --check exits 3 when price < floor, no exceptions. --check exits 4
when the price is none of the candidates and price.json carries no
`override: {"by": "<person>", "reason": "..."}` block. Exit 0 when it passes.
When generation rejected every candidate (price.rejected.json, no candidates
file) the check runs against an empty list, so only an override above the
floor passes.

Generation exits 0 with at least one candidate, 2 when every candidate is
below the floor (writes price.rejected.json instead), 1 on error, including
a viability.json with ok false.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pricing import Model, PricingError, load_fees, load_policy, unit_cost_basis  # noqa: E402

try:
    from etsy_search import market_weight  # noqa: E402
except Exception:  # the plugin copy may lack the search script; the formula is small
    def market_weight(rec):
        est = rec.get("sales_est") if isinstance(rec.get("sales_est"), dict) else None
        upd = est.get("units_per_day") if est else None
        if isinstance(upd, (int, float)) and not isinstance(upd, bool):
            return round(max(0.0, float(upd)), 6)
        sold = rec.get("sold")
        fav = rec.get("favorers")
        sold = sold if isinstance(sold, (int, float)) and not isinstance(sold, bool) and sold > 0 else 0
        fav = fav if isinstance(fav, (int, float)) and not isinstance(fav, bool) and fav > 0 else 0
        return round(math.log1p(sold) + 0.5 * math.log1p(fav) + (1 if sold > 0 else 0), 3)

P75_RULE = ("(c) of the p75 rule holds when distinct_named_shops >= 2, top_share < 0.6 and unnamed_share < 0.2; "
            "one dominant shop, or sellers without a name, means the median")


def die(msg: str, code: int = 1) -> None:
    print(json.dumps({"ok": False, "error": msg}), file=sys.stderr)
    sys.exit(code)


def load(path: Path, what: str) -> dict:
    if not path.is_file():
        die(f"{what} missing: {path}")
    try:
        doc = json.loads(path.read_text())
    except ValueError as e:
        die(f"{what} is not JSON: {e}")
    if not isinstance(doc, dict):
        die(f"{what} is not a JSON object")
    return doc


def _num(v):
    """A real number or None; booleans are not numbers here."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def build(wd: Path, args) -> tuple[Model, dict, dict, float]:
    quote = load(wd / "quote.json", "quote.json")
    viab = load(wd / "viability.json", "viability.json")
    try:
        policy, ppath = load_policy(args.policy)
        fees, fpath = load_fees(policy, args.fees)
        model = Model(policy, fees)
    except PricingError as e:
        die(str(e))
    try:
        cb = unit_cost_basis(quote, policy, getattr(args, "cost_basis", None))
    except PricingError as e:
        die(str(e))
    args._cost_basis = cb
    return model, quote, viab, float(cb["unit_price"])


def _read_optional(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _compact(rec: dict) -> dict:
    """The compact record market_weight() scores: shop.sold before sold, num_favorers before favorers.

    Both the full Etsy record and the comparables shape are accepted, so the
    precedence is fixed here rather than left to whichever formula copy is imported.
    """
    shop = rec.get("shop") if isinstance(rec.get("shop"), dict) else {}

    def present(v):
        # Same rule as the scorers: a non-null value is present, and a value
        # that is not a real number (a bool, a string) counts as 0.
        return 0.0 if isinstance(v, bool) or not isinstance(v, (int, float)) else float(v)

    sold = present(shop["sold"]) if shop.get("sold") is not None else present(rec.get("sold"))
    fav = present(rec["num_favorers"]) if rec.get("num_favorers") is not None else present(rec.get("favorers"))
    return {"sold": sold, "favorers": fav, "sales_est": rec.get("sales_est")}


def _weight(rec: dict) -> float:
    w = _num(rec.get("market_weight"))
    return float(w) if w is not None else float(market_weight(_compact(rec)))


def _shop_names(wd: Path, comps: list[dict]) -> dict:
    """listing_id -> shop name, from the comparables themselves or the full search result."""
    names = {}
    for c in comps:
        shop = c.get("shop")
        if isinstance(shop, dict) and shop.get("name"):
            names[c.get("listing_id")] = shop["name"]
    missing = {c.get("listing_id") for c in comps if c.get("listing_id") not in names}
    if missing:
        full = _read_optional(wd / "research" / "etsy.full.json")
        for r in (full or {}).get("kept") or []:
            if not isinstance(r, dict):
                continue
            shop = r.get("shop") if isinstance(r.get("shop"), dict) else {}
            if r.get("listing_id") in missing and shop.get("name"):
                names[r["listing_id"]] = shop["name"]
    return names


def p75_sellers(wd: Path, p75: float) -> tuple[dict | None, str | None]:
    """Who holds the prices at or above p75 among the selected comparables, as weight shares by shop."""
    doc = _read_optional(wd / "comparables.json")
    if doc is None:
        return None, "comparables.json missing or unreadable; (c) of the p75 rule cannot be judged"
    comps = [c for c in doc.get("comparables") or []
             if isinstance(c, dict) and _num(c.get("price")) is not None]
    if not comps:
        return None, "comparables.json has no priced comparables; (c) of the p75 rule cannot be judged"
    at_or_above = [c for c in comps if float(c["price"]) + 1e-9 >= p75]
    names = _shop_names(wd, at_or_above)
    weights = {}
    counts = {}
    for c in at_or_above:
        key = names.get(c.get("listing_id"))  # None groups every unnamed listing together
        weights[key] = weights.get(key, 0.0) + _weight(c)
        counts[key] = counts.get(key, 0) + 1
    total = sum(weights.values())
    # A set of never-sold, never-favourited listings carries no weight at all;
    # then each listing counts once so the shares still describe who is there.
    if total > 0:
        shares, basis = {k: v / total for k, v in weights.items()}, "market_weight"
    else:
        shares, basis = {k: v / len(at_or_above) for k, v in counts.items()} if at_or_above else {}, "count"
    shops = [{"name": k, "weight_share": round(s, 3)}
             for k, s in sorted(shares.items(), key=lambda kv: (-kv[1], kv[0] is None, str(kv[0])))]
    named = [s for s in shops if s["name"]]
    return {
        "p75": p75,
        "n": len(at_or_above),
        "weight_total": round(total, 3),
        "share_basis": basis,
        "shops": shops,
        "distinct_named_shops": len(named),
        "top_share": max((s["weight_share"] for s in named), default=0.0),
        "unnamed_share": round(shares.get(None, 0.0), 3),
        "rule": P75_RULE,
    }, None


def _band_position(price: float, market: dict) -> str | None:
    p25, p75 = _num(market.get("p25")), _num(market.get("p75"))
    if p25 is None or p75 is None:
        return None
    return "below_p25" if price < p25 else "above_p75" if price > p75 else "within_p25_p75"


def generate(wd: Path, args) -> int:
    model, quote, viab, unit = build(wd, args)
    if not viab.get("ok"):
        die(f"viability.json is not usable: {viab.get('reason')}")
    market = viab.get("market") if isinstance(viab.get("market"), dict) else {}
    basis = market.get("basis") or "unweighted"
    n = market.get("n")
    median, p75 = _num(market.get("median")), _num(market.get("p75"))
    cost = model.cost(unit)
    floor = model.round_price(model.break_even(cost), "up")

    skipped = []
    raw = [("markup", model.round_price(model.markup_price(cost), "up"),
            f"quote {unit:.2f} plus {model.markup * 100:.0f}% markup, rounded up")]
    if not market:
        skipped.append({"label": "median", "reason": "viability.json has no market block"})
    elif median is None:
        skipped.append({"label": "median", "reason": "viability.json market has no median"})
    else:
        raw.append(("median", model.round_price(median, "nearest"),
                    f"market median of {n} comparables, {median:.2f}, {basis} band, rounded"))
    if not market:
        skipped.append({"label": "p75", "reason": "viability.json has no market block"})
    elif p75 is None:
        skipped.append({"label": "p75", "reason": "viability.json market has no p75"})
    else:
        p75_price = model.round_price(p75, "nearest")
        same = [label for label, price, _ in raw if abs(price - p75_price) < 0.005]
        if same:
            skipped.append({"label": "p75", "reason": f"market p75 {p75:.2f} rounds to {p75_price:.2f}, "
                                                      f"the same price as the {' and '.join(same)} candidate"})
        else:
            raw.append(("p75", p75_price, f"market p75 of {n} comparables, {p75:.2f}, {basis} band, rounded"))

    candidates, rejected = [], []
    for label, price, how in raw:
        pt = model.point(label, price, cost, how=how,
                         vs_median=round(price / median, 2) if median else None,
                         position_in_band=_band_position(price, market))
        if price + 1e-9 < floor:
            pt["rejected_because"] = "below_floor"
            pt["below_floor_by"] = round(floor - price, 2)
            rejected.append(pt)
        elif pt["margin_pct"] is not None and pt["margin_pct"] < model.min_margin:
            pt["rejected_because"] = "below_min_margin"
            pt["min_margin_pct"] = model.min_margin
            rejected.append(pt)
        else:
            candidates.append(pt)

    if p75 is None:
        sellers, sellers_note = None, "viability.json market has no p75; (c) of the p75 rule cannot be judged"
    else:
        sellers, sellers_note = p75_sellers(wd, float(p75))
        if sellers is not None and not any(c["label"] == "p75" for c in candidates):
            sellers_note = "p75 is not a candidate (see skipped and rejected); kept for the record"

    out = {
        "ok": bool(candidates),
        "product": viab.get("product"),
        "currency": market.get("currency"),
        "quote_unit_price": round(unit, 2),
        "cost_basis": args._cost_basis["basis"],
        "cost_bases": {
            "one_off": {"quote_unit_price": round(args._cost_basis["one_off"], 2),
                        "floor": model.round_price(model.break_even(model.cost(args._cost_basis["one_off"])), "up")},
            "full_plate": {"quote_unit_price": round(args._cost_basis["full_plate"], 2),
                           "floor": model.round_price(model.break_even(model.cost(args._cost_basis["full_plate"])), "up"),
                           "max_per_plate": args._cost_basis["max_per_plate"],
                           "plate_discount_pct": args._cost_basis["plate_discount_pct"]},
        },
        "unit_cost": round(cost, 2),
        "floor": floor,
        "floor_rule": "break-even (cost + fees + VAT owed) rounded up to the policy ending; a price below it is refused by --check for any author",
        "min_margin_pct": model.min_margin,
        "min_margin_rule": "candidates under this margin are rejected for the model; a person may still choose one with an override block",
        "market": {**{k: market.get(k) for k in ("n", "min", "p25", "median", "p75", "max")}, "basis": basis},
        "candidates": candidates,
        "rejected": rejected,
        "skipped": skipped,
        "p75_sellers": sellers,
        "p75_sellers_note": sellers_note,
        "policy": model.describe(),
        "verdict": viab.get("verdict"),
    }
    if candidates:
        dst = Path(args.out) if args.out else wd / "price.candidates.json"
        dst.write_text(json.dumps(out, indent=2))
        print(json.dumps({"ok": True, "out": str(dst), "floor": floor, "basis": basis,
                          "candidates": [(c["label"], c["price"], c["margin_pct"]) for c in candidates],
                          "rejected": [(c["label"], c["price"], c["rejected_because"]) for c in rejected],
                          "skipped": [s["label"] for s in skipped],
                          "p75_sellers": {k: sellers[k] for k in ("distinct_named_shops", "top_share", "unnamed_share")} if sellers else None}))
        return 0
    out["reason"] = "no candidate clears break-even and the minimum margin"
    dst = wd / "price.rejected.json"
    dst.write_text(json.dumps(out, indent=2))
    print(json.dumps({"ok": False, "out": str(dst), "reason": out["reason"], "floor": floor,
                      "rejected": [(c["label"], c["price"], c["rejected_because"]) for c in rejected]}))
    return 2


def check(wd: Path, args) -> int:
    model, quote, viab, unit = build(wd, args)
    ppath = Path(args.check) if args.check and args.check != "-" else wd / "price.json"
    chosen = load(ppath, "price.json")
    # When every candidate was rejected there is no candidates file, only
    # price.rejected.json. A person may still set a price above the floor, so
    # the check then runs against an empty candidate list: override required.
    cands_path = wd / "price.candidates.json"
    if not cands_path.is_file() and (wd / "price.rejected.json").is_file():
        cands = {**load(wd / "price.rejected.json", "price.rejected.json"), "candidates": []}
    else:
        cands = load(cands_path, "price.candidates.json")
    price = chosen.get("price")
    if not isinstance(price, (int, float)) or price <= 0:
        die("price.json has no positive `price`")
    cost = model.cost(unit)
    floor = model.round_price(model.break_even(cost), "up")
    errors = []
    if price + 1e-9 < floor:
        errors.append(f"price {price:.2f} is below the hard floor {floor:.2f} (break-even); refused for any author")
    listed = [c["price"] for c in cands.get("candidates", [])]
    override = chosen.get("override")
    if not any(abs(price - p) < 0.005 for p in listed):
        if isinstance(override, dict) and override.get("by") and override.get("reason"):
            note = f"price {price:.2f} is not a candidate; accepted on override by {override['by']}"
        else:
            errors.append(f"price {price:.2f} is not one of the candidates {listed} and price.json has no override block with `by` and `reason`")
            note = None
    else:
        note = None
    if chosen.get("currency") and cands.get("currency") and chosen["currency"] != cands["currency"]:
        errors.append(f"currency {chosen['currency']} differs from candidates' {cands['currency']}")
    result = {"ok": not errors, "price": price, "floor": floor, "candidates": listed,
              "margin": round(model.margin(price, cost), 2), "margin_pct": model.margin_pct(price, cost) and round(model.margin_pct(price, cost), 3),
              "errors": errors, "note": note}
    print(json.dumps(result))
    if any("hard floor" in e for e in errors):
        return 3
    return 4 if errors else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--policy", help="default $DAPILOT_STORE_DIR/reference/pricing-policy.json")
    ap.add_argument("--cost-basis", choices=["one_off", "full_plate"], help="override the policy's cost basis; --check uses the same rule")
    ap.add_argument("--fees", help="default store copy, else the plugin copy")
    ap.add_argument("--out", help="generation: where to write (default <workdir>/price.candidates.json)")
    ap.add_argument("--check", nargs="?", const="-", help="verify a price.json (default <workdir>/price.json) instead of generating")
    args = ap.parse_args()
    wd = Path(args.workdir)
    return check(wd, args) if args.check is not None else generate(wd, args)


if __name__ == "__main__":
    sys.exit(main())
