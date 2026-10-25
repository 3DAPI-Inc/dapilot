---
name: analyze-model
description: Produces analysis.json (bounding box, volume, body count, watertightness) for one model file in a work dir by running the bundled mesh script. Use as the first intake stage on any stl, 3mf or obj. Not for rendering, quoting or judging printability; the slicer decides that.
allowed-tools: Read, Bash, Write
---

# analyze-model

## When
A brief with `stage: analyze`. The work dir holds `source.<ext>` and no
`analysis.json`.

## Inputs
- `source.stl` | `source.3mf` | `source.obj`

## Procedure
1. If `analysis.json` exists, print a `cached:true` result and stop.
2. Run
   `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/analyze_mesh.py $WORKDIR/source.<ext> --out $WORKDIR/analysis.json`.
   The script writes `analysis.json` itself, atomically and only on
   success; never redirect its output into the file. Non-zero exit: no file
   was written, return `ok:false` with the script's `reason`, stop. Never
   open the model file yourself.
3. Read `analysis.json`. The one judgement here: if `bodies` > 1 and
   `largest_body_frac` < 0.9, this is an assembly. Put "multi-part, N
   bodies" in the summary so later stages expect several pieces.
4. Summary line: bodies, watertight bodies, volume in cm3 (mm3 / 1000, or
   "volume unknown, open mesh" when null), bbox in mm rounded to integers.
5. Append the log paragraph, remove `.lock`, print the result line.

## Outputs
- `analysis.json`, written by the script, the same JSON it printed. Fields: `triangles`,
  `bbox_mm`, `volume_mm3`, `area_mm2`, `watertight`, `winding_consistent`,
  `bodies`, `watertight_bodies`, `largest_body_frac`.

## Gate
Auto. No network, no spend.

## Failure
- Unsupported extension: `ok:false`, reason `unsupported: <ext>`.
- Script failure: pass its reason through unchanged.
- Missing Python deps (trimesh, scipy, networkx): the reason will say so;
  pass it through, it is an environment problem for a person.

## Do not
- Do not estimate anything the script did not print.
- Do not decide printability. That is `quote-model`, via the slicer.
- Do not read `source.*` into your context. It is triangle soup.

`$PY` is `${DAPILOT_PYTHON:-python3}`; it must have trimesh, scipy, networkx.
