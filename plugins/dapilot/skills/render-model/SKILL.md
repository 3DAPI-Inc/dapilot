---
name: render-model
description: Produces studio product images for one model, a hero and three supporting angles, by running the bundled Blender scene and choosing the angle and orientation that flatter the object. Use as the render stage after analysis.json exists. Not for thumbnails (3MF previews are free) and not for deciding colour policy.
allowed-tools: Read, Bash, Write
---

# render-model

## When
A brief with `stage: render`. The work dir has `source.<ext>` and
`analysis.json`, and no `renders/hero.png`.

## Inputs
- `source.<ext>`, `analysis.json` (bbox and body count, to sanity-check the drafts)
- `${DAPILOT_STORE_DIR}/config.json` `render` block if present: `color`, `layer_lines`, `angles`

## Procedure
1. If `renders/hero.png` exists, print a `cached:true` result and stop.
2. Draft turntable, cheap: run
   `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/render_model.py --workdir $WORKDIR --mode draft --frames 8`
   (about 20 s). It writes `renders/draft_00.png` to `draft_07.png` at 45
   degree steps of camera yaw, starting from the front.
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
- `renders/choice.json`: `{"rotate": "x,y,z", "hero_yaw": N, "reason": "..."}`
- `renders/render.json`: settings and timings, written by the script

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
