# design-product eval cases

Scripts: `design_product.py` (context, check, produce, finalize),
`capture_turntable.py`, `generate_image.py`, `generate_video.py`, `fal_queue.py`.
Captures need the studio page (`cd frontend && npm run dev`), Chrome and
ffmpeg. Paid cases need `FAL_KEY` or `FAL_API_KEY`.

## 1. Happy path, studio only (free), 2026-09-13

Work dir: a copy of the cat clicker (`74f4ddf9`, `cat (clickerfactory.com).3mf`)
with a fixture `price.json` at 7.99 USD. Brief with `lifestyle_images: []` and
`lifestyle_video: null`, `hero_yaw 0`, `angles [90, 180, 270]`.

- `check`: exit 0, `cost.usd` 0.
- `produce`: exit 0 in 172 s on the reference laptop (stills 17 s for four
  2048 px frames, turntable 147 s for 240 frames at 1080 px).
  `review_cells`: hero, angle_1..3, turntable@1/4..4/4.
- `finalize`: exit 0. `design/photos/01_hero.png` .. `04_angle_3.png`, all
  2048x2048; `video.id` turntable, 10.0 s, 0.5 MB; spend 0.
- Yaw check, by a person: browser hero at yaw 0 shows the lid left and the
  body right, as `renders/draft_00.png`; yaw 90 shows the body in front with
  the lid hidden, as `draft_02.png`. The two renderers agree on yaw.

## 2. Hostile brief

A brief with an unknown field, `|` and `#` in the name, `rotate "45,0,0"`,
`hero_yaw 400`, two angles, a colour outside the palette, four lifestyle
images with an unknown reference, and `motion: "spin"`.

- `check`: exit 1 and nine errors, one per defect; `produce` refuses to run
  ("run check first").
- `finalize --reject angle_2` without `--reason`: exit 1.
- `finalize --reject hero --reason x`: exit 1, the hero cannot be rejected.

## 3. Cached and resumed

- `finalize` with `design.json` present: exit 1, "a person deletes it".
- `produce` rerun after exit 0: `steps: []`, nothing captured again.
- fal resume, mocked (`fal_client.submit/status/result` patched), for
  `generate_image.py`: run 1 with a deadline exits 3 after one submit and
  writes `lifestyle_1.png.request.json`; run 2 attaches to `req-123`, downloads,
  exit 0; run 3 prints cached. Submits across the three runs: 1.

## 4. Paid happy path (to run once, by a person, about 1.1 USD)

Same work dir, brief with three lifestyle images and a drift clip. Expected:
`check` total about 1.11 USD under a 2.00 cap; `produce` may exit 3 once or
twice while Wan runs (200 to 650 s seen, 1478 s with a queue); `review.png`
shows 7 photos and 8 clip frames; `finalize` picks the lifestyle clip
(5.0 s) as the listing video unless rejected. Record the reference answers
here once a person has looked at the review sheet.
