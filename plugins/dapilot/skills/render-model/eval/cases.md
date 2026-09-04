# render-model eval cases

Script level, no model. `$R` is `plugins/dapilot/scripts/render_model.py`.

1. Draft on `fixtures/multi.stl` work dir: 8 PNGs in `renders/`, `render.json.draft.render_s` under 60 s on the reference laptop.
2. Final on the Bambu shark 3MF: `renders/render_input.ply` is created, `hero.png` shows the whole shark standing on the floor, not sunk (regression: stale matrix before the floor offset).
3. Framing: in `hero.png` the object's projected box fills the frame with about 10 percent margin on the tightest side, for both a flat wide object and a tall one.
4. `--rotate 90,0,0` on a model exported lying down produces an upright hero.
5. Cached: with `renders/hero.png` present the skill prints `cached:true` and renders nothing.

Last run: 2026-09-04, cases 1 to 3 pass.
