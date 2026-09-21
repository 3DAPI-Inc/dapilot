#!/usr/bin/env python3
"""The copy stage around its one judgement: the words.

    listing_copy.py context --workdir work/<hash>   facts for the writer -> copy/context.json
    listing_copy.py check   --workdir work/<hash>   validate copy/draft.json -> listing.json, social/posts.json

The skill (write-listing) writes copy/draft.json between the two:
    {"title": "...", "description": "...", "tags": ["..."], "materials": ["pla"],
     "social": {"instagram": {"reel": {"caption": "...", "hashtags": ["..."]},
                              "carousel": {"caption": "...", "hashtags": ["..."]}}}}

Everything that is not wording is decided here or upstream: the category is
research's taxonomy id, checked against the seller taxonomy when the store
has it; the price is never in the copy; which photos a post carries is fixed
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


def taxonomy(wd: Path) -> tuple[int | None, str | None]:
    """Research's category, with its path from the seller taxonomy when the store keeps one."""
    tid = (read(wd / "comparables.json") or {}).get("taxonomy_id")
    store = os.environ.get("DAPILOT_STORE_DIR")
    tree = read(Path(store) / "reference" / "etsy_seller_taxonomy.json") if store else None
    if not isinstance(tid, int) or not tree:
        return (tid if isinstance(tid, int) else None), None

    def walk(nodes, path=()):
        for n in nodes:
            here = path + (n.get("name"),)
            if n.get("id") == tid:
                return " > ".join(here)
            found = walk(n.get("children") or [], here)
            if found:
                return found
        return None
    path = walk(tree.get("results") if isinstance(tree, dict) else tree)
    return (tid if path else None), path


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
    ctx = {
        "ok": True, "name": design.get("name"), "identity": comps.get("identity"),
        "size_mm": sorted(size, reverse=True) if size else None,
        "weight_g": ((read(wd / "quote.json") or {}).get("metrics") or {}).get("model_g"),
        "parts": [{"name": i.get("name"), "colour": i.get("colour")} for i in assembly.get("items") or []] or None,
        "multicolour": bool(assembly.get("multicolour")),
        "category": {"taxonomy_id": tid, "path": path},
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
            posts.append({"platform": platform, "format": fmt, "caption": row["caption"].strip(), "hashtags": hashtags, "media": refs})

    everything = " ".join([title, description] + [p["caption"] for p in posts]).lower()
    if "—" in everything:
        e.append("there is an em dash in the copy")
    for claim in CLAIMS:
        if claim in everything:
            e.append(f"the copy claims {claim!r}; a printed part cannot back that")
    tid, _ = taxonomy(wd)
    if tid is None:
        e.append("research's taxonomy id is missing or is not in the seller taxonomy; a person sets the category")
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
    a = ap.parse_args()
    if not Path(a.workdir).is_dir():
        return emit({"ok": False, "reason": f"no work dir at {a.workdir}"}, 2)
    return {"context": cmd_context, "check": cmd_check}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
