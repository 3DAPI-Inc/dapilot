# analyze-model eval cases

Script-level cases run without a model. `$PY` has trimesh, scipy, networkx.
`$A` is `plugins/dapilot/scripts/analyze_mesh.py`.

## 1. Happy path, single body
`$PY $A fixtures/single.stl` -> ok, triangles 81920, bodies 1, watertight true, volume 269318.8

## 2. Happy path, assembly
`$PY $A fixtures/multi.stl` -> ok, triangles 5168, bodies 8, watertight_bodies 8, volume 29357.4.
Skill judgement: multi-part (largest_body_frac < 0.9). Summary must say "multi-part, 8 bodies".

## 3. Real 3MF with a non-watertight union
The Bambu shark export (kept outside the repo, 2.5MB): ok, triangles 122444,
bodies 15, watertight_bodies 15, watertight false, volume 56472.5. Loads in
about 2.5s at about 0.22GB.

## 4. Hostile input
An empty file named `source.stl`: `ok:false` with a reason from the loader,
exit 1. The skill passes the reason through and writes no analysis.json.

## 5. Cached rerun
With analysis.json present the skill prints `cached:true` and runs nothing.

Last run: 2026-09-03, cases 1 to 3 pass at script level.
