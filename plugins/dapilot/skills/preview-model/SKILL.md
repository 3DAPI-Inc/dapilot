---
name: preview-model
description: Produces the cheap 8-frame draft turntable (renders/draft_00.png to draft_07.png) for one model so later stages can see what the object is. A script stage since 2026-10-20: the director runs preview_model.py itself, at no model cost. By hand, the same script. Not for product images (design-product) and not for choosing an angle.
allowed-tools: Read, Bash, Write
---

# preview-model

## When
A brief with `stage: preview`. The work dir has `source.<ext>` and
`analysis.json`, and no `renders/draft_00.png`. In a run the director runs
this stage as a script (`stages.json` names `preview_model.py`), so a worker
only sees it when a person runs the skill by hand.

## Inputs
- `source.<ext>`, `analysis.json`; `display.ply` when `assembly_3mf.py`
  built one (the parts placed as assembled, in their colours)

## Procedure
1. If `renders/draft_00.png` exists, print a `cached:true` result and stop.
2. Run, with a Bash timeout of 300000 ms:
   `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/preview_model.py --workdir $WORKDIR`
   It captures eight WebGL frames of the file as exported in headless
   Chrome, at 45 degree steps of yaw from the front, 18 degrees above the
   horizon, and writes `renders/draft_00.png` to `draft_07.png` at 512 px
   and a `draft` record into `renders/render.json`. About 3 s on the
   reference laptop. Non-zero exit: `ok:false`, reason from the script's
   JSON.
3. Log paragraph, remove `.lock`, print the result line with outputs
   `["renders/draft_00.png"]`. There is no judgement in this stage.

## Outputs
- `renders/draft_00.png` to `renders/draft_07.png`, 512 px, WebGL
- `renders/render.json`, the `draft` record: frames, yaws, size, timing

## Gate
Auto. CPU time only.

## Failure
- Chrome missing: the script exits non-zero saying so; pass the message
  through. The studio page itself ships with the plugin (`studio/`), so a
  missing page is a broken install, not a missing dependency.
- Unsupported extension: `ok:false`, reason from the script.

## Do not
- Do not look at the drafts. Naming the product is research-comparables'
  job; orientation and hero yaw are design-product's.
- Do not render stills here. Stills run only after a price exists.
- Do not pass `--rotate` or `--color`; drafts are always the file as exported.

`$PY` is `${DAPILOT_PYTHON:-python3}`.
