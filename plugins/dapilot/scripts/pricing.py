"""Sale-price arithmetic shared by market_stats.py and price_candidates.py.

One place for the formula so the two scripts cannot disagree. Nothing here
reads the network; everything comes from three files:

  pricing-policy.json   $DAPILOT_STORE_DIR/reference/pricing-policy.json, required.
                        Cost basis, markup, VAT, platform and region, rounding.
  platform-fees.json    $DAPILOT_STORE_DIR/reference/platform-fees.json, else the
                        plugin copy next to research-comparables. Fee schedule
                        per platform and region.
  quote.json            3DAPI's quote: the cost basis.

The model, per unit sold at item price P with the buyer paying shipping at cost:

    net(P)     = P / (1 + vat_rate)      when vat.in_sale_price, else P
    fees(P)    = listing_fee + payment_fixed + P * (transaction_pct + payment_pct + extra_pct)
    cost       = quote unit price * (1 + vat_rate) when purchase VAT is not reclaimable
    margin(P)  = net(P) - fees(P) - cost
    break_even = the P with margin 0, closed form
    price at markup m on cost = cost * (1 + m), then rounded

Fees are charged on the gross price the buyer pays, VAT is owed on it too, so
both are functions of P and the break-even is solved, not looked up.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_FEES = HERE.parent / "skills" / "research-comparables" / "reference" / "platform-fees.json"


class PricingError(Exception):
    pass


def _read(path: Path, what: str) -> dict:
    if not path.is_file():
        raise PricingError(f"{what} missing: {path}")
    try:
        return json.loads(path.read_text())
    except ValueError as e:
        raise PricingError(f"{what} is not JSON: {e}")


def load_policy(path: str | None = None) -> tuple[dict, Path]:
    if path:
        p = Path(path)
    else:
        store = os.environ.get("DAPILOT_STORE_DIR")
        if not store:
            raise PricingError("DAPILOT_STORE_DIR is not set; the pricing policy lives at $DAPILOT_STORE_DIR/reference/pricing-policy.json")
        p = Path(store) / "reference" / "pricing-policy.json"
    pol = _read(p, "pricing policy")
    for k in ("platform", "seller_region", "markup_pct", "vat"):
        if k not in pol:
            raise PricingError(f"pricing policy lacks `{k}`")
    return pol, p


def load_fees(policy: dict, path: str | None = None) -> tuple[dict, Path]:
    """The fee row for policy.platform and policy.seller_region."""
    if path:
        p = Path(path)
    else:
        store = os.environ.get("DAPILOT_STORE_DIR")
        cand = Path(store) / "reference" / "platform-fees.json" if store else None
        p = cand if cand and cand.is_file() else PLUGIN_FEES
    doc = _read(p, "fee schedule")
    platform = policy["platform"]
    region = policy["seller_region"]
    plat = doc.get("platforms", {}).get(platform)
    if not plat:
        raise PricingError(f"fee schedule has no platform `{platform}`")
    row = plat.get("regions", {}).get(region)
    if not row:
        raise PricingError(f"fee schedule has no region `{region}` for `{platform}`")
    fees = {**{k: v for k, v in plat.items() if k != "regions"}, **row}
    for k in ("listing_fee", "transaction_pct", "payment_pct", "payment_fixed"):
        if k not in fees:
            raise PricingError(f"fee row {platform}/{region} lacks `{k}`")
    fees.setdefault("extra_pct", 0.0)
    fees["platform"] = platform
    fees["region"] = region
    return fees, p


def unit_cost_basis(quote: dict, policy: dict, override: str | None = None) -> dict:
    """Which 3DAPI price is the unit cost: the one-off quote or the full-plate unit.

    policy.cost_basis is "one_off" (the quote for one unit, what print-to-order
    costs) or "full_plate" (the unit price at max_per_plate from the batch
    curve, what a stock run costs). "quote" is accepted as the old spelling of
    one_off. A full_plate basis on a one-per-plate design is the one-off price.
    Returns every number so the caller can report the road not taken.
    """
    basis = (override or policy.get("cost_basis") or "one_off").lower()
    if basis == "quote":
        basis = "one_off"
    if basis not in ("one_off", "full_plate"):
        raise PricingError(f"cost_basis must be one_off or full_plate, not {basis}")
    one_off = quote.get("unit_price")
    if not isinstance(one_off, (int, float)) or isinstance(one_off, bool):
        raise PricingError("quote.json has no unit_price")
    batch = quote.get("batch") or {}
    prices = batch.get("prices") or []
    max_per_plate = int(batch.get("max_per_plate") or 1)
    full_plate = float(prices[-1]["unit"]) if prices and max_per_plate > 1 else float(one_off)
    used = full_plate if basis == "full_plate" else float(one_off)
    return {"basis": basis, "unit_price": used, "one_off": float(one_off), "full_plate": full_plate,
            "max_per_plate": max_per_plate, "plate_discount_pct": round((1 - full_plate / float(one_off)) * 100, 1) if one_off else 0.0}


class Model:
    """The formula, bound to one policy and one fee row."""

    def __init__(self, policy: dict, fees: dict):
        self.policy = policy
        self.fees = fees
        vat = policy["vat"]
        self.vat_rate = float(vat.get("rate", 0.0))
        self.vat_in_price = bool(vat.get("in_sale_price", False))
        self.vat_on_purchase = not bool(vat.get("purchase_reclaimable", True))
        self.pct = float(fees["transaction_pct"]) + float(fees["payment_pct"]) + float(fees.get("extra_pct", 0.0))
        self.fixed = float(fees["listing_fee"]) + float(fees["payment_fixed"])
        if self.pct >= 1:
            raise PricingError("fee percentages sum to 100% or more")
        self.markup = float(policy["markup_pct"]) / 100.0
        self.min_margin = float(policy.get("min_margin_pct", 0.0))
        self.rounding = policy.get("rounding", "0.99")

    # ---- cost side
    def cost(self, quote_unit_price: float) -> float:
        c = float(quote_unit_price)
        if self.vat_on_purchase:
            c *= 1 + self.vat_rate
        return c

    # ---- revenue side
    def net(self, price: float) -> float:
        return price / (1 + self.vat_rate) if self.vat_in_price else price

    def fee_total(self, price: float) -> float:
        return self.fixed + price * self.pct

    def margin(self, price: float, cost: float) -> float:
        return self.net(price) - self.fee_total(price) - cost

    def margin_pct(self, price: float, cost: float) -> float | None:
        n = self.net(price)
        return self.margin(price, cost) / n if n else None

    def break_even(self, cost: float) -> float:
        """P with margin 0:  net(P) - fixed - pct*P - cost = 0."""
        k = (1 / (1 + self.vat_rate)) if self.vat_in_price else 1.0
        denom = k - self.pct
        if denom <= 0:
            raise PricingError("fees exceed net revenue; no break-even exists")
        return (cost + self.fixed) / denom

    def price_for_margin_pct(self, cost: float, m: float) -> float:
        """P with margin/net == m."""
        k = (1 / (1 + self.vat_rate)) if self.vat_in_price else 1.0
        denom = k * (1 - m) - self.pct
        if denom <= 0:
            raise PricingError(f"margin {m} is unreachable under these fees")
        return (cost + self.fixed) / denom

    def markup_price(self, cost: float) -> float:
        return cost * (1 + self.markup)

    # ---- rounding
    def round_price(self, value: float, direction: str = "nearest") -> float:
        """Round to the policy ending (e.g. 0.99). direction: nearest | up."""
        if self.rounding in (None, "", "none"):
            return round(value, 2)
        ending = float(self.rounding)  # 0.99 or 0.49 etc
        base = math.floor(value)
        cands = [base - 1 + ending, base + ending, base + 1 + ending]
        cands = [c for c in cands if c > 0]
        if direction == "up":
            up = [c for c in cands if c >= value - 1e-9]
            return round(min(up), 2)
        return round(min(cands, key=lambda c: abs(c - value)), 2)

    def point(self, label: str, price: float, cost: float, **extra) -> dict:
        return {
            "label": label,
            "price": round(price, 2),
            "net_of_vat": round(self.net(price), 2),
            "fees": round(self.fee_total(price), 2),
            "unit_cost": round(cost, 2),
            "margin": round(self.margin(price, cost), 2),
            "margin_pct": round(self.margin_pct(price, cost), 3) if self.margin_pct(price, cost) is not None else None,
            "markup_on_cost_pct": round((price - cost) / cost * 100, 1) if cost else None,
            **extra,
        }

    def describe(self) -> dict:
        return {
            "platform": self.fees["platform"],
            "seller_region": self.fees["region"],
            "listing_fee": float(self.fees["listing_fee"]),
            "payment_fixed": float(self.fees["payment_fixed"]),
            "pct_of_price": round(self.pct, 4),
            "vat_rate": self.vat_rate,
            "vat_in_sale_price": self.vat_in_price,
            "vat_added_to_purchase": self.vat_on_purchase,
            "cost_basis": (self.policy.get("cost_basis") or "one_off").replace("quote", "one_off"),
            "markup_pct": self.markup * 100,
            "min_margin_pct": self.min_margin,
            "rounding": self.rounding,
        }
