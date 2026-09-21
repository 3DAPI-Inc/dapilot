---
name: design-product
description: Names one priced model and art-directs its Etsy listing media, four AI lifestyle photos and a lifestyle clip as the standard with studio stills as extras, by writing one design brief, reviewing the result and redoing a rejected asset once; the design script captures, generates, caps the spend and assembles design.json. Use as the design stage once price.json, comparables.json and the preview drafts exist. Not for the Etsy title, description or tags (copy), not for pricing, not for publishing.
allowed-tools: Read, Bash, Write
---

# design-product

## When
A brief with `stage: design`. The work dir has `source.<ext>`, `analysis.json`,
`renders/draft_00.png` to `draft_07.png`, `price.json` and `comparables.json`,
and no `design.json`. Only items with a sale price get here.

## Inputs
- `design/context.json`, written by step 2: product noun and query with
  research's `identity` (the object and how sure it was), the top
  comparable titles, size and scale word, the drafts and their yaws, the
  3MF's own `thumbnails` when the project carried them (colour renders,
  worth a Read before naming), the
  palette, the limits, the cost of each paid asset and the cap
- `renders/draft_*.png`: what the object looks like, as exported
- `design/review.png` and `design/produced.json`, written by step 5

Environment: `FAL_KEY` or `FAL_API_KEY` for the paid assets; the studio page
(`design.studio_url` in config, the frontend's `/studio`), Chrome and ffmpeg
for captures. The script fails with a one-line reason when one is missing.

## Procedure
1. If `design.json` exists, print a `cached:true` result and stop.
2. Run `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/design_product.py context --workdir $WORKDIR`.
   Non-zero exit: `ok:false`, reason from its JSON.
3. Judge: write the brief. Read `design/context.json` and drafts 00, 02, 04
   and 06 (the others only if those leave the front unclear). Decide, with
   `reference/art-direction.md`:
   - `name`: what a buyer calls it, 2 to 5 words
   - `rotate` and `hero_yaw`: stand it the way a buyer expects, pick the side
     that reads best; yaw is the draft's yaw
   - `angles`: the other stills, each showing something the hero does not
   - `elevation` and `color` from the palette. When `context.assembly` says
     `multicolour`, the parts keep the file's own filament colours and
     `color` only picks the backdrop: choose the palette colour nearest the
     largest part
   - `assembled`: leave it out. `context.assembly.required_assembly` already
     decides whether the media shows the product put together or laid out
     as printed. Set `false` only when the buyer receives loose parts and
     the listing should show them so (a kit), `true` never needs setting
   - `lifestyle_images`: exactly `limits.lifestyle_images` scenes (4), one
     scene each, and which still it starts from. The count is the listing
     standard, not your call
   - `lifestyle_video`: one scene, and `start_from`: the lifestyle photo the
     clip opens on (the clip animates that photo, so pick the scene with
     room to move in). Required, never `null`
   - `studio_photos`: `[]`, or the stills (`hero`, `angle_N`) worth adding
     after the lifestyle photos because they show something no scene does
   - `reason`: one or two sentences
   Write `design/brief.json`, schema under Outputs.
4. Run `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/design_product.py check --workdir $WORKDIR`.
   Exit 1: fix only what `errors` names and run check once more. A second
   failure: `ok:false` with the errors. An error about the cap is for a
   person: the standard is config, so do not drop assets and do not edit
   config; `ok:false` with the error.
5. Run, with a Bash timeout of 600000 ms:
   `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/design_product.py produce --workdir $WORKDIR`
   - Exit 0: continue.
   - Exit 3: the deadline passed with a capture or a fal request still
     running. Run the same command again, at most 5 runs in all. Nothing is
     captured or paid for twice. Still 3 after 5 runs: `ok:false`, reason
     "still generating at <step>"; the retry resumes it.
   - Exit 2: environment (key, balance, ffmpeg): `ok:false`, reason as printed.
   - Exit 1: `ok:false`, reason and `step` as printed.
6. Judge once: read `design/review.png`. Its cells run left to right, four
   to a row, in the order of `review_cells` in the output; `name@k/4` is
   frame k of a clip. Answer two questions only:
   - Is the hero usable: the whole object in frame, lit, standing as a buyer
     expects? Every generated asset starts from it. No: `ok:false`, reason
     naming what is wrong. Do not finalize.
   - Which generated assets misrepresent the product? Reject a lifestyle
     image or the lifestyle clip that changes its shape, parts or colour,
     adds copies, text or a mangled hand, or (clip) morphs between frames.
     Reject an angle only if it shows nothing. Never reject for taste.
7. If step 6 rejected a lifestyle image or the clip, or `produce` listed
   one under `failed`, make it again, once:
   `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/design_product.py redo --workdir $WORKDIR --reject id,id --reason "<what was wrong, one sentence>"`.
   The reason goes into the next prompt, so say what to keep true ("the
   insert has four diamonds, not eight petals"). Then run step 5 and step 6
   once more for the ids in `redo`. Ids under `already_redone` or `over_cap`
   are not made again.
8. Run `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/design_product.py finalize --workdir $WORKDIR`
   adding `--reject id,id --reason "<one sentence>"` for whatever is still
   wrong after the redo, and `--no-redo` only when `redo` answered
   `over_cap` for it. Studio stills stand in for missing lifestyle photos and
   `design.json` says `standard.met: false`; that is a result, not a failure.
   Non-zero exit: `ok:false`, reason from its JSON.
9. Append the log paragraph: name, rotate, hero yaw, angles, colour, scenes
   in a few words each, produce runs and steps, what was rejected, redone and
   why, photo count, listing video, whether the standard was met, spend. Remove `.lock`. Print the result line
   with outputs `["design.json"]` and a summary like
   `"Cat Fidget Clicker: 6 photos, lifestyle clip, 1.11 USD"`.

## Outputs
- `design/brief.json`, the one file this skill writes by hand:
  ```json
  {"name": "Cat Fidget Clicker", "rotate": "0,0,0", "hero_yaw": 20, "angles": [110, 200, 290],
   "elevation": 30, "color": "#E8E4DC",
   "lifestyle_images": [{"scene": "...", "reference": "hero"}, {"scene": "...", "reference": "angle_1"},
                        {"scene": "...", "reference": "hero"}, {"scene": "...", "reference": "angle_2"}],
   "lifestyle_video": {"scene": "...", "start_from": "lifestyle_1"},
   "studio_photos": [],
   "reason": "one or two sentences"}
  ```
- By the script: `design/context.json`, `design/plan.json`, `design/produced.json`,
  `design/review.png`, `design/frames/`, `design/turntable.mp4`,
  `design/lifestyle_N.png`, `design/lifestyle.mp4`, their `.request.json` and
  records, `design/photos/NN_<id>.png` in listing order, `design/video.mp4`.
- `design.json`, by `finalize`: name, colour, orientation, `photos` in order
  (lifestyle photos, stand-in stills, then `studio_photos`), the listing
  `video` (the lifestyle clip when kept and within Etsy's limits, else the
  turntable), `standard` (wanted, kept, `met`), `videos`, `rejected`,
  `redone`, `failed`, `spend`, `warnings`.
  Its existence completes the stage.

## Gate
Auto, spend capped. The paid assets run only for an item with a price, only
through `design_product.py`, and `check` refuses a plan above
`design.usd_cap_per_item`. Nothing here touches dapi or Etsy.

## Failure
- Studio page not running, Chrome or ffmpeg missing: `ok:false` with the
  script's reason; it is an environment problem for a person.
- fal key missing or balance empty (exit 2): `ok:false`. The studio assets
  already captured stay and are reused by the retry.
- One lifestyle asset failed on fal: the script records it under `failed`
  and carries on. Step 7 makes it again once. Not a stage failure; mention
  it in the log.
- No clip fits Etsy's limits: `finalize` fails; `ok:false` with its reason.

## Do not
- Do not type a price, a cost, a pixel size or a frame count anywhere. The
  script owns them; the brief has no field for them.
- Do not run `capture_turntable.py`, `generate_image.py` or
  `generate_video.py` yourself, and never pass `--force`.
- Do not run `produce` again after exit 0 to get a better picture, and do
  not rewrite the brief after `produce` started. One design pass per item,
  plus the one `redo` of step 7 for assets that misrepresent the product,
  never for taste.
- Do not write the Etsy title, tags or description. `name` is the product's
  name, not a search string.
- Do not read `design/frames/` or the full-size photos. The review sheet is
  the view.

`$PY` is `${DAPILOT_PYTHON:-python3}`.
