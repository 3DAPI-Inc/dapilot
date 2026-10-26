#!/usr/bin/env python3
"""What a stage's output files must look like, checked after the stage says it is done.

  check_outputs.py --workdir work/<hash> --stage research        print {"hard": [...], "soft": [...]}

Two lists, and the difference is the policy (decided 2026-10-23):

  hard   the file is not what the next stage can read: it does not parse, or a field every reader
         depends on is missing or of the wrong type. The ledger treats a hard problem like a missing
         output: the attempt failed, the stage is queued again within its attempts.
  soft   the file is readable and the stage is done, but something in it deserves a look: no
         comparables, a price under its own floor, fewer photos than the standard, a listing with
         three tags. The ledger records the stage done, writes `<stage>.review.json` beside the
         output and goes on. Nothing waits for the review; the hosted supervisor or a person decides
         whether the stage is worth redoing (`ledger.py retry`, which redoes a done stage when it
         holds a review marker and nothing later has used its outputs).

Lax on purpose: a check here must be something a reader would agree is wrong on sight, never a
style preference, so that review is rare and a failed attempt rarer. A stage not listed has no
checks beyond the file existing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# What market_stats.py emits: `unreliable` since the review-weighted band (2026-09-14), missing here until 2026-10-26,
# which sent every unreliable item back through research once and cost a worker attempt.
VERDICTS = ("viable", "thin", "not_viable", "unreliable")
PLATE_MM = 256.0        # the common bed; over it is a question, not a failure (the slicer decides)
MIN_TAGS = 5            # Etsy allows 13; under five the listing is hard to find
TITLE_MAX = 140         # Etsy's title limit, which listing_copy.py check enforces


def load(wd: Path, name: str, hard: list[str]) -> dict | None:
    p = wd / name
    if not p.exists():
        hard.append(f"{name} is missing")
        return None
    try:
        doc = json.loads(p.read_text() or "null")
    except (OSError, ValueError) as e:
        hard.append(f"{name} does not parse: {e}")
        return None
    if not isinstance(doc, dict):
        hard.append(f"{name} is not a JSON object")
        return None
    return doc


def number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check(wd: Path, stage: str) -> dict:
    hard: list[str] = []
    soft: list[str] = []
    if stage == "analyze":
        d = load(wd, "analysis.json", hard)
        if d is not None:
            bbox = d.get("bbox_mm")
            if not (isinstance(bbox, list) and len(bbox) == 3 and all(number(x) for x in bbox)):
                hard.append("analysis.json: bbox_mm is not three numbers")
            elif max(bbox) > PLATE_MM:
                soft.append(f"analysis.json: the longest side is {max(bbox):.0f} mm, over a {PLATE_MM:.0f} mm plate")
            if not (isinstance(d.get("bodies"), int) and d["bodies"] >= 1):
                hard.append("analysis.json: bodies is not a count of at least one")
            elif isinstance(d.get("watertight_bodies"), int) and d["watertight_bodies"] < d["bodies"]:
                soft.append(f"analysis.json: {d['bodies'] - d['watertight_bodies']} of {d['bodies']} bodies are not closed")
    elif stage == "quote":
        d = load(wd, "quote.json", hard)
        if d is not None:
            if d.get("status") != "DONE":
                hard.append(f"quote.json: status is {d.get('status')!r}, not DONE")
            m = d.get("metrics")
            if not isinstance(m, dict):
                hard.append("quote.json: metrics is missing")
            else:
                if not (number(m.get("model_g")) and m["model_g"] > 0):
                    soft.append("quote.json: metrics.model_g is not a positive weight")
                if not (number(m.get("print_time_s")) and m["print_time_s"] > 0):
                    soft.append("quote.json: metrics.print_time_s is not a positive time")
    elif stage == "research":
        c = load(wd, "comparables.json", hard)
        v = load(wd, "viability.json", hard)
        if c is not None and not isinstance(c.get("comparables"), list):
            hard.append("comparables.json: comparables is not a list")
        if v is not None:
            if v.get("verdict") not in VERDICTS:
                hard.append(f"viability.json: verdict {v.get('verdict')!r} is not one of {', '.join(VERDICTS)}")
            n = v.get("n_comparables")
            if (isinstance(n, int) and n == 0) or (c is not None and isinstance(c.get("comparables"), list) and not c["comparables"]):
                soft.append("research found no comparables; the price will rest on cost alone")
            elif v.get("verdict") == "not_viable":
                soft.append("research says not viable: the market median sits under the one-off break-even")
            elif v.get("verdict") == "unreliable":
                soft.append("research says the market band is unreliable" + (f": {v.get('verdict_reason')}" if v.get("verdict_reason") else "")
                            + "; the price rests on cost")
    elif stage == "price":
        d = load(wd, "price.json", hard)
        if d is not None:
            price, floor = d.get("price"), d.get("floor")
            if not (number(price) and price > 0):
                hard.append("price.json: price is not a positive number")
            if not number(floor):
                hard.append("price.json: floor is missing")
            elif number(price) and price < floor:
                soft.append(f"price.json: the price {price} is under its own floor {floor}")
            if not str(d.get("rationale") or "").strip():
                soft.append("price.json: no rationale")
    elif stage == "design":
        d = load(wd, "design.json", hard)
        if d is not None:
            if d.get("ok") is not True:
                hard.append("design.json: ok is not true")
            if not str(d.get("name") or "").strip():
                hard.append("design.json: no product name")
            photos = d.get("photos")
            if not isinstance(photos, list):
                hard.append("design.json: photos is not a list")
            elif not photos:
                soft.append("design.json: no photos kept")
            std = d.get("standard") or {}
            if isinstance(std, dict) and std.get("met") is False:
                want = (std.get("lifestyle_images") or {}).get("want")
                kept = (std.get("lifestyle_images") or {}).get("kept")
                clip = (std.get("lifestyle_video") or {}).get("kept")
                soft.append(f"design.json: the standard was not met ({kept} of {want} lifestyle photos"
                            f"{', no clip' if clip is False else ''})")
    elif stage == "copy":
        d = load(wd, "listing.json", hard)
        if d is not None:
            title = str(d.get("title") or "").strip()
            if not title:
                hard.append("listing.json: no title")
            elif len(title) > TITLE_MAX:
                soft.append(f"listing.json: the title is {len(title)} characters, over Etsy's {TITLE_MAX}")
            if not str(d.get("description") or "").strip():
                hard.append("listing.json: no description")
            tags = d.get("tags")
            if not isinstance(tags, list):
                hard.append("listing.json: tags is not a list")
            elif len(tags) < MIN_TAGS:
                soft.append(f"listing.json: only {len(tags)} tags")
    elif stage == "publish":
        d = load(wd, "published.json", hard)
        if d is not None:
            if d.get("ok") is not True:
                hard.append("published.json: ok is not true")
            failed = d.get("failed") or [s for s in (d.get("steps") or []) if isinstance(s, dict) and s.get("ok") is False]
            if failed:
                soft.append(f"published.json: {len(failed)} step(s) did not succeed")
            etsy = d.get("etsy") or {}
            if not d.get("sandbox") and isinstance(etsy, dict) and not etsy.get("listing_id"):
                soft.append("published.json: live mode but no Etsy listing id")
    return {"stage": stage, "hard": hard, "soft": soft}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--stage", required=True)
    a = ap.parse_args()
    res = check(Path(a.workdir).resolve(), a.stage)
    print(json.dumps(res, indent=2))
    return 1 if res["hard"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
