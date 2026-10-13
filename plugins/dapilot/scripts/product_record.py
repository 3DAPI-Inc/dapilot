#!/usr/bin/env python3
"""The product record for one item whose sale price a person has approved.

Deterministic. Nothing here is judged: every field is copied from files the
pipeline already wrote. The record is what the shop sells, the middle between
3DAPI (who makes it) and the storefront listing (who sells it), and it holds
what an order for an end customer needs.

    product_record.py --workdir work/<hash> [--verify-key] [--out FILE]

Reads  price.approved.json   required; written when a person confirms the price
       price.json, quote.json, meta.json, analysis.json
       comparables.json, viability.json, design.json   when present
Writes $DAPILOT_STORE_DIR/products/<sha256>.json (or --out)

Run it again whenever the inputs change (a new approved price, the design
stage finishing): `created_at`, `owner` and the `storefront` block of an
existing record are kept, everything else is rebuilt.

The record:
  id              the model file's sha256, the key of everything else
  status          awaiting_media until design.json exists, then ready
  owner           the 3DAPI API key this product belongs to, from GET /me
                  (--verify-key; through dapi.py like every other 3DAPI call)
  name, price, cost, quote
  order_item      what 3DAPI's createOrder needs per item: the quote publicId
                  and how many filaments the design takes. The filament codes
                  and the customer are chosen per order. 3DAPI creates the
                  design and the SKU itself from the quote when the order is
                  placed, so no product has to exist on 3DAPI first.
  media           photos in listing order and the listing video, as paths
                  relative to the work dir
  storefront      where it is listed (Etsy); filled by whoever lists it
  dapi_listing    the same product in the shape 3DAPI's product save expects
                  (listing, manufacture, designs, skus, images, ...). Files
                  carry tempKey null and are repeated under pending_uploads:
                  a tempKey comes from an upload, and the public API has no
                  product route yet, so nothing is uploaded here. skus is
                  empty for the same reason: a SKU names platform material
                  ids, and 3DAPI makes one per filament choice at order time.

Exit 0 with the record written, 1 on error (no approved price, missing quote).
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DAPI = HERE / "dapi.py"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def die(msg: str) -> None:
    print(json.dumps({"ok": False, "error": msg}), file=sys.stderr)
    sys.exit(1)


def read(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text()) if path.is_file() else None
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def key_owner() -> dict:
    """The identity of the API key in env, from GET /me."""
    if not os.environ.get("DAPI_API_KEY") or not os.environ.get("DAPI_API_URL"):
        return {"verified": False, "note": "DAPI_API_KEY or DAPI_API_URL not set"}
    p = subprocess.run([sys.executable, str(DAPI), "call", "getMe"], capture_output=True, text=True)
    try:
        r = json.loads(p.stdout)
    except ValueError:
        return {"verified": False, "note": f"dapi.py produced no JSON: {(p.stderr or p.stdout)[:200]}"}
    key = (r.get("body") or {}).get("api_key") if isinstance(r.get("body"), dict) else None
    if not r.get("ok") or not isinstance(key, dict):
        return {"verified": False, "note": f"GET /me answered HTTP {r.get('status')}"}
    return {"verified": bool(key.get("exists") and key.get("active")), "api_key_name": key.get("name"),
            "api_url": os.environ["DAPI_API_URL"], "verified_at": now()}


def content_type(name: str) -> str:
    known = {".3mf": "model/3mf", ".stl": "model/stl", ".obj": "model/obj", ".step": "model/step", ".stp": "model/step"}
    return known.get(Path(name).suffix.lower()) or mimetypes.guess_type(name)[0] or "application/octet-stream"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--verify-key", action="store_true", help="ask 3DAPI who the API key belongs to and record it as the owner")
    ap.add_argument("--out", help="default $DAPILOT_STORE_DIR/products/<sha256>.json")
    a = ap.parse_args()
    wd = Path(a.workdir)
    meta = read(wd / "meta.json")
    if not meta or not meta.get("sha256"):
        die(f"meta.json missing or without sha256: {wd}")
    approved = read(wd / "price.approved.json")
    if not approved or not isinstance(approved.get("price"), (int, float)):
        die("price.approved.json missing: a product exists only once a person has approved its price")
    quote = read(wd / "quote.json")
    if not quote or not quote.get("publicId"):
        die("quote.json missing or without publicId")
    if isinstance(approved.get("floor"), (int, float)) and approved["price"] + 1e-9 < approved["floor"]:
        die(f"approved price {approved['price']:.2f} is below its floor {approved['floor']:.2f}")

    item = meta["sha256"]
    if a.out:
        dst = Path(a.out)
    else:
        store = os.environ.get("DAPILOT_STORE_DIR")
        if not store:
            die("DAPILOT_STORE_DIR is not set; products live at $DAPILOT_STORE_DIR/products/")
        dst = Path(store) / "products" / f"{item}.json"
    old = read(dst) or {}

    price = read(wd / "price.json") or {}
    design = read(wd / "design.json") or {}
    design = design if design.get("ok") else {}
    comps = read(wd / "comparables.json") or {}
    viab = read(wd / "viability.json") or {}
    analysis = read(wd / "analysis.json") or {}
    display = read(wd / "display.json") or {}
    metrics = quote.get("metrics") or {}
    sources = sorted(p.name for p in wd.iterdir() if p.stem == "source")
    if not sources:
        die("no source.<ext> in workdir")
    source_name = meta.get("file") or sources[0]

    name = design.get("name") or comps.get("product") or viab.get("product") or Path(source_name).stem
    # The unit of sale (decided 2026-10-12): one sale brings `units` printed units. The approval
    # carries it, else the proposal, else research's offer; a record from before carries none and sells singles.
    units = next((o["units"] for o in (approved.get("offer"), price.get("offer"), viab.get("offer"))
                  if isinstance(o, dict) and isinstance(o.get("units"), int) and o["units"] > 0), 1)
    photos = [{"id": p.get("id"), "kind": p.get("kind"), "file": p.get("file")} for p in design.get("photos", []) if p.get("file")]
    video = design.get("video") if isinstance(design.get("video"), dict) and design["video"].get("file") else None

    design_temp = f"design-{item[:12]}"
    image_files = [{"tempId": f"image-{i + 1}", "filename": Path(p["file"]).name, "contentType": content_type(p["file"]),
                    "tempKey": None} for i, p in enumerate(photos)]
    pending = [{"tempId": design_temp, "what": "design file", "path": sources[0]}]
    pending += [{"tempId": img["tempId"], "what": "image", "path": p["file"]} for img, p in zip(image_files, photos)]

    owner = old.get("owner") or {"verified": False, "note": "not verified yet"}
    if a.verify_key:
        fresh = key_owner()
        owner = fresh if fresh.get("verified") or not owner.get("verified") else owner

    record = {
        "ok": True,
        "id": item,
        "status": "ready" if photos else "awaiting_media",
        "created_at": old.get("created_at") or now(),
        "updated_at": now(),
        "owner": owner,
        "name": name,
        "source_file": source_name,
        "price": {k: approved.get(k) for k in ("price", "currency", "by", "at", "source", "floor", "unit_cost", "margin", "margin_pct")},
        # What one sale is: `units` printed units for `price`. Everything that sells or orders reads this.
        "offer": {"units": units, "per_unit": round(approved["price"] / units, 2),
                  "source": (viab.get("offer") or {}).get("source") if units > 1 else "single",
                  "extras_common": (viab.get("offer") or {}).get("extras_common") or []},
        "rationale": price.get("rationale"),
        "quote": {"publicId": quote["publicId"], "unit_price": quote.get("unit_price"), "store_id": quote.get("store_id"),
                  "max_per_plate": (quote.get("batch") or {}).get("max_per_plate"),
                  "model_g": metrics.get("model_g"), "print_time_s": metrics.get("print_time_s")},
        # the assembled product when assembly_3mf.py built one; analysis.json's bbox is the print layout
        "size_mm": (display.get("bbox_mm") if display.get("pose") == "assembled" else None) or analysis.get("bbox_mm"),
        "order_item": {"quoteId": quote["publicId"], "filament_slots": metrics.get("filament_count") or 1, "quantity": units,
                       "note": "one line per sale: quantity is the units one purchase brings, times the storefront quantity sold; "
                               "createOrder takes one filament code per slot, chosen per order from listFilaments"},
        "media": {"photos": photos, "video": {"id": video.get("id"), "file": video["file"]} if video else None,
                  "color": design.get("color")},
        "storefront": old.get("storefront") or {"platform": "etsy", "state": "not_listed", "listing_id": None, "url": None},
        "dapi_listing": {
            "listing": {"name": name, "custom_web_address": None, "description": None, "category_id": None,
                        "prices_include_vat": (viab.get("policy") or {}).get("vat_in_sale_price"),
                        "sales_price": approved["price"], "is_public": False, "stock_list": [], "dropdown_names": [],
                        "disclaimers": []},
            "manufacture": {"manufactureSettings": {"extra_parts": False, "assembly": False}, "extraParts": [], "extraIds": [],
                            "farmInstructions": ""},
            "designs": {"deleteDesignIds": [], "newDesignFiles": [{"tempId": design_temp, "filename": source_name,
                                                                   "contentType": content_type(source_name), "tempKey": None}]},
            "skus": {"variantOptions": []},
            "images": {"deleteImageIds": [], "newImages": image_files,
                       "pendingPrimaryImageId": image_files[0]["tempId"] if image_files else None},
            "extraFiles": {"deleteExtraFileIds": [], "newExtraFiles": []},
            "complianceFiles": {"deleteDocumentIds": [], "newDocuments": []},
            "materialConfigs": {"partMaterialConfigs": []},
            "imageAssignments": {},
        },
        "pending_uploads": pending,
    }
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    print(json.dumps({"ok": True, "out": str(dst), "id": item, "name": name, "status": record["status"],
                      "price": approved["price"], "owner_verified": bool(owner.get("verified"))}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
