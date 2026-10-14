---
name: preview-model
description: Produces the cheap 8-frame draft turntable (renders/draft_00.png to draft_07.png) for one model so later stages can see what the object is. Use as the preview stage after analysis.json exists. Not for product images (render-model) and not for choosing an angle.
allowed-tools: Read, Bash, Write
---

# preview-model

## When
A brief with `stage: preview`. The work dir has `source.<ext>` and
`analysis.json`, and no `renders/draft_00.png`.

## Inputs
- `source.<ext>`, `analysis.json`

## Procedure
1. If `renders/draft_00.png` exists, print a `cached:true` result and stop.
2. Run, with a Bash timeout of 300000 ms:
   `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/render_model.py --workdir $WORKDIR --mode draft --frames 8`
   It writes `renders/draft_00.png` to `draft_07.png` at 45 degree steps of
   camera yaw and `renders/render.json`. About 30 to 70 s on the reference
   laptop. Non-zero exit: `ok:false`, reason from stderr.
3. Log paragraph, remove `.lock`, print the result line with outputs
   `["renders/draft_00.png"]`. There is no judgement in this stage.

## Outputs
- `renders/draft_00.png` to `renders/draft_07.png`, 320 px, 24 samples
- `renders/render.json`, settings and timings, written by the script

## Gate
Auto. CPU time only.

## Failure
- Blender missing or not runnable: the script exits non-zero with the path
  it tried; pass the message through.
- Unsupported extension: `ok:false`, reason "unsupported: <ext>".

## Do not
- Do not look at the drafts. Naming the product is research-comparables'
  job; orientation and hero yaw are render-model's.
- Do not render finals here. Finals run only after a price exists.
- Do not pass `--rotate` or `--color`; drafts are always the file as exported.

`$PY` is `${DAPILOT_PYTHON:-python3}`.
