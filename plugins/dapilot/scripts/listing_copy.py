#!/usr/bin/env python3
"""The copy stage around its one judgement: the words.

    listing_copy.py context --workdir work/<hash>   facts for the writer -> copy/context.json
    listing_copy.py check   --workdir work/<hash>   validate copy/draft.json -> listing.json, social/posts.json
    listing_copy.py taxonomy "<words>"              seller taxonomy nodes matching the words, with ids

The skill (write-listing) writes copy/draft.json between the two:
    {"title": "...", "description": "...", "tags": ["..."], "materials": ["pla"],
     "social": {"instagram": {"reel": {"caption": "...", "hashtags": ["..."]},
                              "carousel": {"caption": "...", "hashtags": ["..."]}}}}

Everything that is not wording is decided here or upstream: the category is
research's taxonomy id when research found the aisle; when it did not (no
comparables, 2026-09-23), the writer picks one from the candidates `context`
lists out of the seller taxonomy and puts it in the draft as `taxonomy_id`,
and `check` accepts it only when it is a node of that taxonomy, so no person
is asked; the price is never in the copy; which photos a post carries is fixed
(a reel is the listing video, a carousel the first photos in listing order).
`check` refuses what Etsy or a platform would refuse and a short list of
claims a 3D print cannot back, then writes the two files the publish stage
reads. Exit 0 ok, 1 refused (errors listed), 2 usage or missing inputs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from etsy_search import parse_offer  # noqa: E402  the same reading of "Set of 6" research applies to the comparables

TITLE_MAX, TAG_MAX, TAGS_MAX, MATERIALS_MAX = 140, 20, 13, 13
SOCIAL = {"instagram": {"caption_max": 2200, "hashtags_max": 30,
                        "formats": {"reel": ["video"], "carousel": "photos"}}}
CAROUSEL_MAX = 5
# Promises a printed PLA part cannot keep, or that need a certificate we do not hold.
CLAIMS = ("dishwasher safe", "dishwasher-safe", "food safe", "food-safe", "food grade", "heat resistant", "heat-resistant",
          "heatproof", "microwave", "waterproof", "unbreakable", "bpa free", "bpa-free", "non toxic", "non-toxic",
          "child safe", "child-safe", "handmade from wood", "solid wood")


def emit(doc: dict, code: int = 0) -> int:
    print(json.dumps(doc, ensure_ascii=False))
    return code


def read(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def write(p: Path, doc: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")


def bad_chars(text: str, categories: tuple, extra: str = "") -> list:
    return sorted({c for c in text if not unicodedata.category(c).startswith(categories) and c not in extra})


def seller_tree() -> list | None:
    store = os.environ.get("DAPILOT_STORE_DIR")
    tree = read(Path(store) / "reference" / "etsy_seller_taxonomy.json") if store else None
    if tree is None:
        return None
    return tree.get("results") if isinstance(tree, dict) else tree


def taxonomy_path(tree: list | None, tid) -> str | None:
    if not isinstance(tid, int) or not tree:
        return None

    def walk(nodes, path=()):
        for n in nodes:
            here = path + (n.get("name"),)
            if n.get("id") == tid:
                return " > ".join(here)
            found = walk(n.get("children") or [], here)
            if found:
                return found
        return None
    return walk(tree)


STOP = {"a", "an", "the", "of", "for", "and", "or", "with", "to", "in", "on", "3d", "printed", "print", "end", "ends", "v2"}


def taxonomy_candidates(tree: list | None, words: str, limit: int = 8) -> list[dict]:
    """Seller taxonomy nodes whose path carries the most of `words`, leaves first: what the writer chooses from."""
    if not tree:
        return []
    def stem(w: str) -> str:
        w = w.strip("().,/&").lower()
        return w[:-1] if w.endswith("s") and len(w) > 3 else w

    stems = {stem(w) for w in words.split()} - STOP - {""}
    rows: list[tuple[float, int, str]] = []

    def walk(nodes, path=()):
        for n in nodes:
            here = path + (n.get("name") or "",)
            # whole words only: "jig" must not match "jigsaw"
            own_words = {stem(w) for w in (n.get("name") or "").split()}
            path_words = {stem(w) for name in here for w in name.split()}
            hits = len(stems & path_words)
            kids = n.get("children") or []
            if hits:
                # a hit in the node's own name beats one inherited from its parents; a leaf beats a branch
                rows.append((hits + 0.5 * len(stems & own_words) + (0.25 if not kids else 0), n.get("id"), " > ".join(here)))
            walk(kids, here)
    walk(tree)
    rows.sort(key=lambda r: (-r[0], len(r[2])))
    return [{"taxonomy_id": tid, "path": path} for _, tid, path in rows[:limit]]


def taxonomy(wd: Path) -> tuple[int | None, str | None]:
    """The category: research's taxonomy id when it is a node of the seller taxonomy, else the draft's."""
    tree = seller_tree()
    for tid in ((read(wd / "comparables.json") or {}).get("taxonomy_id"),
                (read(wd / "copy" / "draft.json") or {}).get("taxonomy_id")):
        if not isinstance(tid, int):
            continue
        if not tree:
            return tid, None   # no seller taxonomy in the store: nothing to check against
        path = taxonomy_path(tree, tid)
        if path:
            return tid, path
    return None, None


def cmd_taxonomy(a) -> int:
    rows = taxonomy_candidates(seller_tree(), a.words, a.limit)
    return emit({"ok": bool(rows), "candidates": rows} | ({} if rows else {"reason": "no seller taxonomy in the store, or no node matches"}), 0 if rows else 1)


def offer_of(wd: Path, approved: dict | None) -> dict:
    """How many of the product one purchase brings, from the approval, else research; 1 before 2026-10-12."""
    viab = read(wd / "viability.json") or {}
    units = next((o["units"] for o in ((approved or {}).get("offer"), viab.get("offer"))
                  if isinstance(o, dict) and isinstance(o.get("units"), int) and o["units"] > 0), 1)
    price = (approved or {}).get("price")
    return {"units": units, "price": price, "per_unit": round(price / units, 2) if isinstance(price, (int, float)) else None,
            "extras_common": (viab.get("offer") or {}).get("extras_common") or [],
            "note": "one item per purchase; do not mention sets or quantities" if units == 1 else
            f"a set of {units}: the title says the count (\"Set of {units}\"), the description says what the buyer receives, "
            "and nothing suggests extras the comparables ship with (a holder, a stand) unless the parts list has them"}


def cmd_context(a) -> int:
    wd = Path(a.workdir)
    design, approved = read(wd / "design.json"), read(wd / "price.approved.json")
    if not design or not approved:
        return emit({"ok": False, "reason": "design.json and price.approved.json are needed before the copy is written"}, 2)
    comps = read(wd / "comparables.json") or {}
    shown = read(wd / "display.json") or {}
    size = (shown.get("bbox_mm") if shown.get("pose") == "assembled" else None) or (read(wd / "analysis.json") or {}).get("bbox_mm")
    assembly = read(wd / "assembly.json") or {}
    tid, path = taxonomy(wd)
    identity = comps.get("identity") or {}
    words = " ".join(str(x) for x in (design.get("name"), comps.get("product"), identity.get("object")) if x)
    ctx = {
        "ok": True, "name": design.get("name"), "identity": comps.get("identity"),
        # The unit of sale: `units` of the product per purchase at the approved price. The title and
        # description say the count when it is more than one; `check` refuses a draft that does not.
        "offer": offer_of(wd, approved),
        "size_mm": sorted(size, reverse=True) if size else None,
        "weight_g": ((read(wd / "quote.json") or {}).get("metrics") or {}).get("model_g"),
        "parts": [{"name": i.get("name"), "colour": i.get("colour")} for i in assembly.get("items") or []] or None,
        "multicolour": bool(assembly.get("multicolour")),
        # Research's aisle when it found one; else candidates from the seller taxonomy for the writer to pick
        # from (draft.taxonomy_id), so a product with no comparables still gets a category without a person.
        "category": {"taxonomy_id": tid, "path": path,
                     "candidates": [] if tid else taxonomy_candidates(seller_tree(), words),
                     "note": None if tid else "research found no aisle: put one candidate's taxonomy_id in the draft, "
                                              "or search with `listing_copy.py taxonomy \"<words>\"`"},
        "comparable_titles": [c.get("title") for c in (comps.get("comparables") or [])[:10]],
        "comparable_tags": sorted({t for c in (comps.get("comparables") or [])[:10] for t in (c.get("tags") or [])})[:60],
        "photos": [p.get("id") for p in design.get("photos") or []], "video": (design.get("video") or {}).get("id"),
        "look_at": "design/review.png",
        "limits": {"title_max": TITLE_MAX, "tags_max": TAGS_MAX, "tag_max_chars": TAG_MAX, "materials_max": MATERIALS_MAX,
                   "social": SOCIAL, "never_claim": list(CLAIMS)},
        "draft_path": "copy/draft.json",
    }
    write(wd / "copy" / "context.json", ctx)
    return emit({"ok": True, "context": "copy/context.json", "name": ctx["name"], "category": path or tid})


def cmd_check(a) -> int:
    wd = Path(a.workdir)
    if (wd / "listing.json").exists():
        return emit({"ok": False, "reason": "listing.json exists; a person sets it aside to have the copy written again"}, 1)
    d = read(wd / "copy" / "draft.json")
    design = read(wd / "design.json") or {}
    if not isinstance(d, dict):
        return emit({"ok": False, "reason": "copy/draft.json is missing or not JSON"}, 2)
    e: list[str] = []
    title = " ".join(str(d.get("title") or "").split())
    description = str(d.get("description") or "").strip()
    tags = [" ".join(str(t).split()) for t in d.get("tags") or []]
    materials = [" ".join(str(m).split()) for m in d.get("materials") or []]

    if not title or len(title) > TITLE_MAX:
        e.append(f"title is {len(title)} characters; Etsy takes 1 to {TITLE_MAX}")
    if bad := bad_chars(title, ("L", "Nd", "P", "Sm", "Z"), "™©®"):
        e.append(f"title has characters Etsy refuses: {bad}")
    if sum(1 for w in title.split() if len(w) > 1 and w.isupper()) > 3:
        e.append("title has more than three words in capitals")
    # The unit of sale, read back the way research reads the comparables: a set's title must carry the count,
    # and a single's title must not read as a set.
    units = offer_of(wd, read(wd / "price.approved.json"))["units"]
    said = parse_offer(title, description, None, None)
    if units > 1 and said.get("units") != units:
        e.append(f"the listing sells {units} per purchase but the title reads as {said.get('units') or 'a set with no count'}: "
                 f"say \"Set of {units}\" (or \"{units} pcs\") in the title")
    if units == 1 and said.get("units") != 1:
        e.append(f"the listing sells one per purchase but the title reads as {said.get('units') or 'a set'}; drop the set wording")
    if len(description) < 200:
        e.append("description is under 200 characters; say what it is, its size, its material and its care")
    if not 5 <= len(tags) <= TAGS_MAX:
        e.append(f"{len(tags)} tags; write 5 to {TAGS_MAX}")
    if len({t.lower() for t in tags}) != len(tags):
        e.append("tags repeat")
    for t in tags:
        if not t or len(t) > TAG_MAX:
            e.append(f"tag {t!r} is {len(t)} characters; Etsy takes 1 to {TAG_MAX}")
        if bad := bad_chars(t, ("L", "Nd", "Zs"), "-'™©®"):
            e.append(f"tag {t!r} has characters Etsy refuses: {bad}")
    if not materials or len(materials) > MATERIALS_MAX:
        e.append(f"materials: 1 to {MATERIALS_MAX}")
    for m in materials:
        if bad_chars(m, ("L", "Nd", "Zs")):
            e.append(f"material {m!r}: letters, digits and spaces only")

    posts = []
    photos = len(design.get("photos") or [])
    social = d.get("social") or {}
    for platform, rules in SOCIAL.items():
        for fmt, media in rules["formats"].items():
            row = (social.get(platform) or {}).get(fmt)
            if not isinstance(row, dict) or not str(row.get("caption") or "").strip():
                e.append(f"social.{platform}.{fmt}: a caption is required")
                continue
            hashtags = [str(h).lstrip("#").strip() for h in row.get("hashtags") or [] if str(h).strip()]
            text = f"{row['caption'].strip()}\n\n" + " ".join(f"#{h}" for h in hashtags)
            if len(text) > rules["caption_max"]:
                e.append(f"social.{platform}.{fmt}: {len(text)} characters with hashtags; the platform takes {rules['caption_max']}")
            if len(hashtags) > rules["hashtags_max"]:
                e.append(f"social.{platform}.{fmt}: {len(hashtags)} hashtags; the platform takes {rules['hashtags_max']}")
            if any(" " in h for h in hashtags):
                e.append(f"social.{platform}.{fmt}: a hashtag has a space in it")
            refs = media if isinstance(media, list) else [f"photo:{n + 1}" for n in range(min(photos, CAROUSEL_MAX))]
            if fmt == "carousel" and len(refs) < 2:
                continue  # one photo cannot make a carousel; the reel still goes out
            if "video" in refs and not design.get("video"):
                continue  # no listing video (a rejected clip gets no turntable since 2026-09-22): no reel
            posts.append({"platform": platform, "format": fmt, "caption": row["caption"].strip(), "hashtags": hashtags, "media": refs})

    everything = " ".join([title, description] + [p["caption"] for p in posts]).lower()
    if "—" in everything:
        e.append("there is an em dash in the copy")
    for claim in CLAIMS:
        if claim in everything:
            e.append(f"the copy claims {claim!r}; a printed part cannot back that")
    tid, _ = taxonomy(wd)
    if tid is None:
        e.append("no category: research found no aisle and the draft's taxonomy_id is missing or is not a node of the "
                 "seller taxonomy; pick one from copy/context.json category.candidates")
    if e:
        return emit({"ok": False, "errors": e}, 1)

    write(wd / "listing.json", {"title": title, "description": description, "tags": tags, "materials": materials,
                                "taxonomy_id": tid, "written_by": "write-listing"})
    write(wd / "social" / "posts.json", {"written_by": "write-listing", "posts": posts})
    return emit({"ok": True, "listing": "listing.json", "posts": "social/posts.json", "title": title,
                 "tags": len(tags), "social_posts": [f"{p['platform']}.{p['format']}" for p in posts]})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("context", "check"):
        sub.add_parser(name).add_argument("--workdir", required=True)
    t = sub.add_parser("taxonomy"); t.add_argument("words"); t.add_argument("--limit", type=int, default=8)
    a = ap.parse_args()
    if a.cmd == "taxonomy":
        return cmd_taxonomy(a)
    if not Path(a.workdir).is_dir():
        return emit({"ok": False, "reason": f"no work dir at {a.workdir}"}, 2)
    return {"context": cmd_context, "check": cmd_check}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
