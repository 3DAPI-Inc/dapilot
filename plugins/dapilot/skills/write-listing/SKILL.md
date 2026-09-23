---
name: write-listing
description: Writes the words for one designed, priced product, the Etsy title, description, tags and materials and the Instagram captions, as one draft that a script checks against each platform's limits and turns into listing.json and social/posts.json. Use as the copy stage once design.json and price.approved.json exist. Not for the product name or the photos (design-product), not for the price or which photos a post carries (scripts), not for publishing. The category is research's aisle, or the writer's pick from the seller taxonomy candidates the script lists when research found none.
allowed-tools: Read, Bash, Write
---

# write-listing

## When
A brief with `stage: copy`. The work dir has `design.json`,
`price.approved.json`, `comparables.json` and `quote.json`, and no
`listing.json`.

## Inputs
- `copy/context.json`, written by step 2: the product's name, research's
  `identity`, the true size and weight, the parts and their colours, the
  category path, the comparables' titles and tags, the photo ids, the limits
  and the claims that are never made
- `design/review.png`: what the listing's photos show. Read it once, so the
  words describe the product in the pictures and not the one in your head
- `reference/voice.md`: how this shop writes

## Procedure
1. If `listing.json` exists, print a `cached:true` result and stop.
2. Run `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/listing_copy.py context --workdir $WORKDIR`.
   Non-zero exit: `ok:false`, reason from its JSON.
3. Judge: write `copy/draft.json`, schema under Outputs, with
   `reference/voice.md`. Read `copy/context.json` and `design/review.png`.
   - `title`: what a buyer types, most important words first; the noun, the
     pattern or style, what it is made by ("3D printed"), one use or gift
     phrase. No keyword pile, at most 140 characters
   - `description`: what it is in two sentences, then DETAILS (one item or
     how many, the size from `size_mm` in cm and inches, material, colours
     as the photos show them), then CARE, then MADE TO ORDER. Every number
     comes from the context. If `identity.confidence` is not high, describe
     only what the photos show
   - `tags`: 13 when you can, each 20 characters or fewer, phrases a buyer
     searches, taken from the comparables' tags where they fit this product;
     never a competitor's shop name or a trademark
   - `materials`: what it is printed in, `pla` unless the context says else
   - `taxonomy_id`: only when `category.taxonomy_id` is null. Research found
     no aisle, so choose the one node of `category.candidates` a buyer would
     browse for this product and put its id here. None fits: run
     `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/listing_copy.py taxonomy "<two or three words>"`
     once and pick from that. Never invent an id
   - `social.instagram.reel` and `.carousel`: a caption of one to three
     short sentences that starts with the thing, not the shop, and ends with
     "Link in bio."; 5 to 12 hashtags without spaces. The carousel caption
     says something the reel caption does not
4. Run `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/listing_copy.py check --workdir $WORKDIR`.
   Exit 1: fix only what `errors` names and run check once more. A second
   failure: `ok:false` with the errors. No question goes to a person from
   this stage (decided 2026-09-23): an approved price is the approval to
   list and post.
5. Append the log paragraph: the title, tag count, the posts written, what
   you left out because the identity was unsure. Remove `.lock`. Print the
   result line with outputs `["listing.json", "social/posts.json"]` and a
   summary like `"Kumiko Lattice Coaster: title, 13 tags, reel and carousel captions"`.

## Outputs
- `copy/draft.json`, the one file this skill writes by hand:
  ```json
  {"title": "...", "description": "...", "tags": ["..."], "materials": ["pla"],
   "taxonomy_id": 877,
   "social": {"instagram": {"reel": {"caption": "...", "hashtags": ["..."]},
                            "carousel": {"caption": "...", "hashtags": ["..."]}}}}
  ```
  `taxonomy_id` only when the context's category was null.
- By the script: `copy/context.json`, `listing.json` (title, description,
  tags, materials, the category id) and `social/posts.json` (each post with
  its media). Their existence completes the stage.

## Gate
Auto. Nothing is sent anywhere; the publish stage sends it.

## Failure
- `design.json` or `price.approved.json` missing: `ok:false`, the script's reason.
- The category id is not a node of the seller taxonomy: `check` says so;
  pick another candidate, once.

## Do not
- Do not state a price, a discount, a delivery time or a shipping cost. The
  listing carries them as fields.
- Do not claim anything in `limits.never_claim`, and nothing about safety,
  certification or heat that the context does not state.
- Do not mention sets, bundles or quantities other than the one item, unless
  the context's parts say the buyer receives several.
- Do not name a designer, a brand, a film, a game or a character.
- Do not use an em dash.
- Do not edit `listing.json` or `social/posts.json`; the script writes them.

`$PY` is `${DAPILOT_PYTHON:-python3}`.
