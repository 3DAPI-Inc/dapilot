---
name: render-model
description: Retired 2026-09-13, replaced by design-product; kept for reference and not dispatched by any stage. Produced studio product images for one model, a hero and three supporting angles, by running the bundled Blender scene and choosing the angle and orientation that flatter the object. Use as the render stage after price.json exists and the drafts from preview-model are in renders/. Not for drafts (preview-model), not for thumbnails (3MF previews are free) and not for deciding colour policy.
allowed-tools: Read, Bash, Write
---

# render-model

> Retired 2026-09-13. The `design` stage (`design-product`, run by the
> `product-designer` agent) replaced `render`: stills and turntable now come
> from the browser studio, lifestyle media from fal. Blender still renders
> the preview drafts through `render_model.py --mode draft`.

## When
A brief with `stage: render`. The work dir has `source.<ext>`,
`analysis.json`, `renders/draft_00.png` to `draft_07.png` from the preview
stage, and `price.json` from the price stage, and no `renders/hero.png`.
Finals run only for items that have a sale price: an item research found
unsellable never reaches this stage.

## Inputs
- `source.<ext>`, `analysis.json` (bbox and body count, to sanity-check the drafts)
- `${DAPILOT_STORE_DIR}/config.json` `render` block if present: `color`, `layer_lines`, `angles`

## Procedure
1. If `renders/hero.png` exists, print a `cached:true` result and stop.
2. The drafts already exist: `renders/draft_00.png` to `draft_07.png` at 45
   degree steps of camera yaw starting from the front, written by
   preview-model. If any is missing, `ok:false` with reason "no drafts";
   do not render them here.
3. Look at the drafts with Read. Decide two things, and only these:
   - **Orientation.** Is the object standing the way a customer expects to
     see it? A figure lying on its back, a vase on its side, a plate on
     edge, means the file was exported in print orientation. Choose a
     `--rotate x,y,z` in degrees that stands it up (usually `90,0,0` or
     `-90,0,0`). Most models need `0,0,0`.
   - **Hero yaw.** The draft where the object's shape reads best: the face
     for a figure, the front three-quarter for a functional part, the open
     side for a container. Note its yaw (index times 45). Add 10 to 20
     degrees of offset if the draft is dead-on, since a slight turn reads
     better than a flat elevation.
   Write both and one sentence of reasoning to `renders/choice.json`.
4. Final render: run
   `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/render_model.py --workdir $WORKDIR --mode final --angles "<hero>,<hero+90>,<hero+180>,<hero+270>" --rotate <x,y,z> [--color HEX] [--layer-lines]`
   with a Bash timeout of 600000 ms; four 1024 px frames take a few minutes.
5. Look at `renders/hero.png` once. Reject and stop with `ok:false` only if
   the object is cut off, invisible, or black. Do not iterate on taste.
6. Log paragraph, remove `.lock`, print the result line with outputs
   `["renders/hero.png", ...]`.

## Outputs
- `renders/hero.png` and `renders/angle_NNN.png`, 1024 px square, studio backdrop
- `renders/turntable.mp4`: the product video, see below. Required for a
  listing; not produced yet (2026-09-08)
- `renders/choice.json`: `{"rotate": "x,y,z", "hero_yaw": N, "reason": "..."}`
- `renders/render.json`: settings and timings, written by the script
- `renders/video.json`: frames, fps, duration, encoder settings, written by
  the video script once it exists

## Video (required, not built yet)
Every listing needs a short product video as well as the stills. It is the
same turntable the stills come from, rendered as a full rotation and encoded
by a script. Decided 2026-09-08. Etsy allows up to two videos per listing and
does not require one, but a listing without video is at a disadvantage in
search and the same clip is what social posting needs, so for this shop it is
mandatory: `publish-listing` must refuse a listing that has no
`renders/turntable.mp4`.

Target, chosen to satisfy Etsy's listing video limits and to be reusable
elsewhere:
- one full 360 degree rotation of the product on its pivot, camera, lights
  and backdrop fixed, same scene as the stills, same `--rotate` and `--color`
- 10 seconds, 24 fps, silent (Etsy strips audio; never add any)
- 1080 by 1080 px, H.264 MP4, yuv420p, under 100 MB (Etsy's limits: 5 to
  15 s, min 500 px, aspect between 1:2 and 2:1, MP4 preferred)
- starts on the hero yaw so the first frame matches `hero.png`

How it will be produced, so the stage stays one script call plus one
judgement:
- `render_model.py --mode video` renders 120 frames at 3 degree steps at
  720 px with a low sample count and denoise, then ffmpeg interpolates to 240
  frames and encodes. ffmpeg is a plugin requirement, checked at startup like
  Blender.
- The model decides nothing new. Orientation and hero yaw come from
  `choice.json`, written in step 3. The video runs after the stills, in the
  same worker, only for items that already passed the hero check in step 5.
- Cost is CPU time. Expect several times the four-frame final on the
  reference laptop; measure before deciding whether video moves to a
  scheduled pass after `publish` (plan principle 5) or stays in the stage.
- Cached on the content hash like everything else. `stages.json` gains
  `renders/turntable.mp4` under `produces` for the render stage the day the
  script lands, not before, or the stage would never complete.

Vertical cuts for TikTok are a crop of this clip, made by the posting script,
not a second render.

## Gate
Auto. CPU time only; no network, no spend.

## Failure
- Blender missing: the script says so; pass it through.
- Blender exit non-zero: pass the `tail` through as the reason.
- A 3MF is converted to PLY by the script; if that fails the reason names it.

## Do not
- Do not change lights, backdrop, lens or material. They are the scene, and
  the scene is the same for every product on purpose.
- Do not render more than one final pass. If the hero is wrong after step 5,
  report it; a person decides.
- Do not read the mesh. Drafts are what you look at.

`$PY` is `${DAPILOT_PYTHON:-python3}`. `BLENDER` in env overrides the binary path.
