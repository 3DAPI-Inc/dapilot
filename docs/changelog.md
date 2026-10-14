# dapilot changelog

The dated build log that lived in CLAUDE.md under "Where this stands" until
2026-10-13, moved here verbatim. CLAUDE.md now describes the current state
only. Entries are in the order they were written, which is not chronological;
each carries its own date.

- **Intake is the first slice** (model file -> listed, priced, photographed
  product). Geometry analysis and rendering exist as scripts. Quoting is
  available through 3DAPI's public API. Listing creation needs a new public
  API route (see "The 3DAPI public API").
- **Phase 0 of `docs/store-plan.md` is built.** Four general skills
  (`hierarchy`, `guardrails`, `output-format`, `work-ledger`), the job
  skills `dapi-request` and `analyze-model`, the `catalogue-worker` and
  `catalogue-lead` agents, the `run-intake` director skill, and the
  deterministic core: `ledger.py` (the only writer of run state) and
  `validate_result.py` (what every level runs on the level below). Only the
  `analyze` and `quote` stages are enabled in `run-intake/reference/stages.json`.
- **Phase 1 quoting is live against staging (2026-09-04).** `quote-model`
  wraps `scripts/quote.py`: upload, create, poll, quotes named by sha256 so
  a file is never sliced twice, resumable through `quote.pending.json`.
  Measured: slicer 18 to 40 s per file, worker about 0.20 USD estimated at
  14 turns.
- **Phase 2 rendering is live (2026-09-04).** `render-model` drives
  `scripts/render_model.py` and `render_scene.py`: a fixed studio scene
  (bundled `studio.exr` environment, key, fill and rim area lights, a
  cyclorama backdrop, 85 mm lens with depth of field, filament material with
  optional layer lines, AgX), exact box framing by projecting the bounding
  box through the camera, 3MF converted to PLY through our loader. The model
  chooses only orientation and hero yaw from an 8-frame draft turntable.
  Draft turntable about 15 to 30 s; one 1024 px final about 60 to 90 s on
  the reference laptop. **Superseded on 2026-09-13 by the design stage
  below; Blender now renders only the preview drafts.** A product video is a
  required output of the render stage (decided 2026-09-08): one 360 degree turntable,
  10 s, 24 fps, 1080 px square, silent H.264 MP4 under 100 MB, within Etsy's
  listing video limits and reusable for social posting. Not built yet; the
  spec is in `render-model/SKILL.md`. Next: the video pass in
  `render_model.py`, `write-listing`, and the stage wrapper to cut worker
  turns.
- **The storefront is Etsy (decided 2026-09-13).** Listings are not posted
  on 3DAPI's storefront for now, so `write-listing` and `publish-listing` are
  unavailable for 3DAPI and stay disabled until they are rebuilt for Etsy.
  3DAPI remains the slicer, the cost basis and the producer.
- **The design stage replaced render (2026-09-13).** The `product-designer`
  agent runs `design-product`: it names the product and writes one brief
  (`design/brief.json`: orientation, hero yaw, three angles, elevation,
  colour from the store palette, up to three lifestyle scenes, one clip
  scene), then reviews one contact sheet and rejects any generated asset that
  misrepresents the product. `design_product.py` does the rest: studio
  stills at 2048 px and a 10 s 1080 px turntable from the browser studio
  (`capture_turntable.py`, headless Chrome over `/studio`, which now takes
  `rotate`, `elevation` and `yaws` and turns the same way as the Blender
  drafts), lifestyle photos on fal (`fal-ai/nano-banana-2/edit`, 2K, about
  0.12 USD each) and a 5 s depth-conditioned Wan clip (about 0.75 USD),
  photos in Etsy order and the listing video (the lifestyle clip if kept,
  else the turntable) in `design.json`. Paid requests are written on submit
  and a rerun attaches (`fal_queue.py`), spend is counted from those files
  against `design.usd_cap_per_item` (2.00 USD), and `produce` exits 3 at its
  deadline so a worker's Bash call never outlives 10 minutes. Stages may name
  their own worker agent in `stages.json`; design workers get 40 minutes
  (`minutes_per_stage`). First headless run on the cat clicker, studio only:
  7 min, 37 turns, 0.43 USD at API rates. First paid run, the kumiko coaster
  (2026-09-21): the designer planned two lifestyle photos and no clip, one
  photo was rejected, and the listing got one lifestyle photo and the turntable.
- **The listing standard is config (2026-09-21).** Every listing carries four
  lifestyle photos and one lifestyle clip (`design.lifestyle_images.count`,
  `design.lifestyle_video.required`); `check` refuses a brief that plans
  anything else. Studio stills are always captured, as the references the
  generated assets start from, but enter the listing only through the brief's
  `studio_photos` or to stand in for a missing lifestyle photo. A rejected or
  failed paid asset is made again once (`design_product.py redo`,
  `design.redo_per_asset`) with the rejection in its prompt, inside the 2.00
  USD cap; the first attempt is kept as `<asset>.rN.*` and still counts as
  spend. `design.json` carries `standard.met`.
- **Design models changed on 2026-09-21.** Photos: `fal-ai/nano-banana-pro/edit`
  (about 0.15 USD each), sent the chosen still plus two other sides. Clip:
  `minimax/h3-max/image-to-video` at 1080p, 6 s, 0.16 USD a second, first on the
  Artificial Analysis image-to-video board that day; it animates the lifestyle
  photo the brief names in `lifestyle_video.start_from`, so the clip can only be
  as right as that photo, and nothing locks the geometry. `generate_video.py
  --engine i2v`; the Wan 2.2 depth route stays as `design.lifestyle_video.engine:
  "vace"`. A standard item is about 1.56 USD, so under the 2.00 USD cap there is
  room to redo photos but not the clip (0.96 USD): the cap needs a decision.
  H3 Max answers in 10 to 20 s where Wan took 7 minutes.
- **Failure is a file in the work dir (2026-09-23).** When a stage ends failed
  (attempts exhausted, stale, outputs missing) or blocked (`needs_human`), the
  ledger writes `<stage>.failed.json` or `<stage>.blocked.json` beside the
  stage's outputs, with the run, attempt and reason. `ledger.py` refuses to
  dispatch a marked stage in any run, `--resume` and the console's Resume skip
  the item, and `GET /jobs/{id}` reports the marker's status and reason. A
  person clears it: Try again on the job page (`POST /jobs/{id}/retry`, which
  removes the markers and starts a director) or `rm`. The ledger removes a
  marker itself when the stage's outputs turn up. Before this, failure lived
  only in one run's `state.json`, the console called the item queued, and every
  Resume re-bought the same failure. Caps were raised the same day for the
  measured turn counts: research 40, price 30, design 50, copy 30 turns,
  stage budgets 1.2, 0.6, 1.5, 0.8 USD, run ceiling 15 USD.
- **The studio turntable is a fallback (ordered 2026-09-23).** `design.turntable.when`
  is `fallback` in config: `design_product.py produce` captures the stills, the
  lifestyle photos and the clip, and renders the 240-frame turntable only when
  fal failed to make the clip and its one redo, or the cap has no room for the
  redo. A clip the reviewer rejected gets no turntable: the listing goes without
  a video, `design.json` carries `video: null`, and `listing_copy.py` drops the
  reel. `"always"` restores the old behaviour. The turntable is close to being
  removed outright; nothing downstream may come to depend on it.
- **Assembly and part colours come from the project file (2026-09-21).** A
  Bambu project places its objects twice: `<build>` is the print layout,
  `<assemble>` in `Metadata/model_settings.config` is the Assembly View, kept
  whenever a designer split one import into objects. The same file names each
  object's extruder and `project_settings.config` the filament colours.
  `strip_3mf.py` removes both, so the server runs `assembly_3mf.py read` on
  the original first and writes `work/<hash>/assembly.json`:
  `required_assembly` (true when the assembled placement differs from the
  print layout and the parts meet there: since 2026-09-23 `parts_touch` requires
  every part within 1 mm of another by bounding box, one connected set, because
  Bambu's Assembly View lines never-assembled objects up in a row and a filament
  welder jig came out as a 479 mm row of three specks, which cost research its
  only comparable on size; `contact` in the file records the test. A person or
  the design brief's `assembled` may still flip it),
  `multicolour`, and per item the two transforms, extruder and colour.
  `assembly_3mf.py build` writes `display.ply`, the parts placed by that flag
  with their filament colour per vertex, and both renderers
  (`render_model.py`, `capture_turntable.py`) show it instead of
  `source.<ext>`; the studio page reads `.ply` and paints vertex colours. The
  slicer, the quote and `analyze_mesh.py` still use `source.<ext>`. First
  case, the kumiko coaster: 386 x 100 x 5 mm as laid out, 100 x 100 x 5 mm
  assembled, walnut lattice and cream star. Not done: `run_intake.py <folder>`
  does not read assemblies yet (only the server upload does), the quote is
  still priced as one filament because the strip drops the extruder
  assignment, and a file with no assemble block gets no assembly.
- **Social posting has its contracts and one platform (2026-09-21).**
  `scripts/social/base.py` defines `Integration` (limits, check, submit with a
  checkpoint before the irreversible step, confirm, metrics) and `Session`
  (status, refresh, keepalive, login_help; one per account per platform, state
  under `$DAPILOT_STORE_DIR/social/sessions/`). `social/instagram_api.py` is
  the first adapter: Meta's free API, a 60-day token renewed by keepalive,
  media fetched by Instagram from the listing's own public Etsy URLs in
  `publish.json`, so no bucket. `social_post.py` is the only caller: `session
  status|keepalive|login`, `check`, `post` (prints without `--confirm`),
  `metrics`. Browser adapters (X, TikTok, LinkedIn page over CloakBrowser)
  load from `DAPILOT_SOCIAL_ADAPTER_DIR`, outside the plugin. Not built: the
  `write-social-post` skill, the browser adapters, the schedule. Plan in
  `docs/social-plan.md`. **Instagram moved to the browser route on 2026-09-23:**
  the Meta API answered `API access blocked` two days after the first token,
  with nothing changed on our side and no remedy but the Meta dashboard, so it
  cannot carry the shop's posting. `instagram_api` stays in the plugin as an
  option; our config names `instagram_web` (CloakBrowser, not built) and keeps
  Instagram disabled until it exists. `publish_product.py` skips the posts of a
  disabled platform instead of failing them.
- **The unit of sale is data (2026-10-12).** The kumiko coaster was priced
  per coaster against a 31 USD median that was six coasters and a holder.
  Now `etsy_search.py` reads every listing's pack (`offer.units`, from the
  variant, the title or the description; "Set" with no count is null and
  stays out), `market_stats.py` builds the band per unit, keeps the
  checkout band as `market.per_listing`, and picks our offer: the modal
  pack by weight when it holds `offer.adopt_share` (pricing policy, 0.5),
  else one. The pack is costed at units x 3DAPI's full-plate unit price with
  the fixed fees once; `price_candidates.py` prices and floors the pack,
  every candidate carries `per_unit`, `price.json` and the approval carry
  `offer.units`, the product record's `order_item.quantity` is that number
  (one Etsy sale = one 3DAPI line of six; createOrder prices N off the batch
  curve in whole plates), the Etsy stock quantity is divided by it, and
  `listing_copy.py check` refuses a title whose count disagrees. The
  `margin` candidate now stands for an unreliable band too. Coaster rerun:
  set of 6, cost 14.94, floor 21.99, 24.99 proposed. Not built: quantity
  variations on one listing, a compact plate layout before quoting (the
  file's 386 mm row gives 2 per plate). Details in `docs/market-research.md` 7d.
- **Approving the price publishes (2026-09-21, built, not yet run end to end).**
  `copy` and `publish` are enabled. `copy` is the `write-listing` skill: one
  draft with the Etsy title, description, tags, materials and the Instagram
  captions, which `listing_copy.py check` validates (Etsy's limits, the
  platform's limits, a list of claims a printed part cannot back, research's
  category against the seller taxonomy) and splits into `listing.json` and
  `social/posts.json`. `publish` has no judgement, so it is the first `script`
  stage: `stages.json` names `publish_product.py`, the ledger passes it in the
  brief, and `run_intake.py` runs it with no worker and no tokens. It drafts,
  uploads media, activates the Etsy listing (0.20 USD, public) and sends every
  social post, then writes `published.json`. The category is research's aisle,
  or, when research found no comparables, the writer's pick from the
  seller-taxonomy candidates `listing_copy.py context` lists (`draft.taxonomy_id`,
  accepted by `check` only as a node of that taxonomy); no question goes to a
  person from copy (decided 2026-09-23, after a filament welder jig sat blocked
  on "Which Etsy category?" while the console said nothing was running). The
  person's approval of the sale
  price is the gate for all of it; `publish.auto_activate` and
  `publish.auto_post` in store config hand either half back to a person, and
  `publish.auto_since` keeps a price approved before that moment from going
  public until it is approved again. The console has a Social tab on the job
  page (`/jobs/{id}/social` routes) to edit captions, post one by hand and read
  the numbers. Not in the browser yet: Etsy draft, media and activate buttons,
  and the Instagram sign-in.
- **Phase 4 research is live ahead of order (2026-09-09).** `research-comparables`
  runs as an intake stage after quote and render: `etsy_search.py` fetches
  and filters Etsy listings (physical, right size, variant closest to our
  size via OAuth inventory), the model picks the true comparables, and
  `market_stats.py` writes `viability.json`: market band, Etsy fees from
  `reference/platform-fees.json` (override in `$DAPILOT_STORE_DIR/reference/`),
  margin at p25, median and p75, break-even and target price, verdict.
  Shipping is reported unknown until a weight table or 3DAPI's shipping
  estimate feeds it. First run, an octagon passthrough fidget: 6
  comparables, median 6.49 USD against a 5.71 USD one-off cost, not viable.
  Needs `ETSY_KEYSTRING`, `ETSY_SHARED_SECRET`, `DAPILOT_STORE_DIR`; the
  Etsy token lives at `$DAPILOT_STORE_DIR/etsy_token.json`, gitignored.
- **The deep market read is live (2026-09-11).** Etsy caps search offsets
  at 5 for our key, so `etsy_search.py` pages by price window (a geometric
  USD ladder, one relevance page each) inside the taxonomy aisle, scores
  every survivor for relevance and market weight, and `market_stats.py`
  builds a weighted band from rules the model writes; `price_candidates.py`
  adds a p75 candidate with a seller-concentration check. The taxonomy id
  is required: without it the duck read returned 404 listings and a 43 USD
  median for a 12 mm charm. Details and numbers in `docs/market-research.md`
  section 7.
- **Market weight is estimated units per day (2026-09-14).** Etsy keeps
  per-listing sales private, but a shop's review feed is public and every
  review carries a listing id and a date, so `etsy_search.py` now reads the
  newest 100 reviews of each survivor's shop (one call per shop; deeper
  offsets are refused for shops we do not own, bearer or not) and writes
  `sales_est` per listing: the shop's units per day from its review rate,
  split over its active listings with add-one smoothing. `market_weight`
  is that number; the weighted median is the price at which half the
  aisle's units sell. Records without an estimate fall back to the old
  log formula and `viability.json` says so in `market.weight_basis`, with
  `weight_concentration` naming the heaviest listing and shop. On the
  shark the weighted median moved from 7.00 to 4.99 and the p75 from
  13.99 to 10.00; the eval fixtures carry legacy weights until rerun.
  Details in `docs/market-research.md` section 7c.
- **The price gate and the catalogue are live (2026-09-17).** The design
  stage requires `price.approved.json`, written when a person approves or
  overrides the proposed price on the job page (`POST /jobs/{id}/price`;
  the server runs `price_candidates.py --check`, so the floor holds for
  people too, and an all-rejected item can be priced by override).
  Approval files the product through `product_record.py` into
  `$DAPILOT_STORE_DIR/products/<sha256>.json` (gitignored), shows it on
  the Catalogue page and starts a director for design and what follows.
  The record carries the quote `publicId` that `createOrder` needs (3DAPI
  builds the design and SKU from the quote at order time) and a
  `dapi_listing` block in the shape of 3DAPI's product save, unsent: the
  public API still has no product route. Placing orders from the
  catalogue is not built. Details in `docs/market-research.md` 4.5 and 4.8.
- **Pricing policy decided 2026-09-09**, in `store/reference/pricing-policy.json`
  (private). Cost basis is 3DAPI's quote as charged, commission included and
  never backed out: whoever produces on the platform pays it. Two candidates
  go to the model in the price stage: quote plus 50% markup, and the market
  median. Hard rule, enforced by `price_candidates.py --check` and never by
  a prompt: the sale price is never below break-even (cost, platform fees,
  VAT owed), for any author of `price.json`; a person may override to
  another price with an `override` block, but not below the floor. The
  policy assumes an EU seller with 21% VAT inside every sale, which is
  conservative for US buyers; under it a 50% markup on the fidget clears
  break-even by 0.25 USD, so the markup and the VAT assumption need a
  second look before the price stage goes live. `cost_basis` in the policy
  chooses the one-off quote or the full-plate unit from the batch curve;
  decided 2026-09-11: `full_plate` with `stock_units: 100`, because the shop
  buys in batches and sells from stock. On the eight-per-plate cat that is
  a floor of 3.99 against 7.99, and `viability.json` carries a `stock`
  block (plates, batch cost, payback units per band price).
- **Quote metrics are per unit only after the API fix of 2026-09-09.** In
  batch mode the API stored the last plate's totals (the 4-copy plate) as
  the unit's grams and time. Fixed in `process-slicing-result.use-case.ts`
  (unit metrics from the one-copy plate); quotes sliced before the redeploy
  keep the wrong `model_g` and must be re-sliced. Batch quoting is a
  per-account flag, `user_api_profiles.batch_quoting`, set in the database
  only.
- **Foreign 3mf projects fail on dapi.** Creality Print, Orca and Prusa
  project files carry printer settings Bambu Studio rejects ("Invalid
  parameter value(s)" or "Failed slicing the model"). `strip_3mf.py` keeps
  the geometry and thumbnails only; the stripped file slices. Bambu Studio
  projects need nothing. Not yet wired into the frontend upload or
  `quote.py`.
- **The geometry scripts were lost and rebuilt on 2026-09-03.** An
  unverified move followed by a delete removed the untracked originals.
  `analyze_mesh.py` was restored verbatim from a transcript;
  `load_3mf.py`, `render_turntable.py` and `make_fixtures.py` were rewritten
  from their documented behaviour and carry a note saying so. The 3MF loader
  reproduces the original's shark numbers exactly. Commit scripts early.
- **The slicer is the authority.** Printability, volume and price come from
  3DAPI's quote, which already runs Bambu Studio. Local geometry analysis is
  a cheap pre-check to avoid spending a slicer run on an unreadable file,
  not a source of truth, and semantic judgement from renders is kept to the
  minimum a listing needs.
- **No orchestrator exists yet.** The interactive Claude Code session is the
  orchestrator for now.
- **Runs are supervised and resumable (2026-09-10).** One `run_intake.py`
  director per upload; `runs/<id>/` keeps `state.json`, `report.md`,
  `director.log` and every worker's raw output under `workers/`. Workers run
  in their own process group and are killed at `max_worker_minutes` (15,
  `store/config.json`); a `.lock` older than that is stale and removed with
  a note. `GET /runs/unfinished` lists work dirs with a stage left and no
  live worker; `POST /runs/resume`, `run_intake.py --resume`, the console's
  Resume button, or `resume_on_start: true` in config start a director over
  them. Two faults found on the first frontend runs and fixed: the ledger
  flipped a running stage to done the moment its file appeared (so every
  record was rejected and no cost kept), and a worker whose `claude -p`
  stalled held its item for two hours because only the direct child was
  killed on timeout. A third, found on the resume: the research worker ran
  out of its 0.50 USD budget reading 65 KB of listing JSON, and its dead
  lock parked the item. Now `etsy_search.py --out` writes a compact record
  per listing (about 3x smaller) with the full projection in `*.full.json`,
  `budget.usd_per_stage` in `store/config.json` sets research at 1.00 USD,
  and the ledger drops a worker's lock when it records that worker's end.
- **The frontend uploads through the server into the pipeline (2026-09-09).**
  `POST /jobs` on `server/app.py` (:3100) strips foreign 3mf project settings,
  files the model under `work/<sha256>/`, and starts one `run_intake.py`
  director per upload; its workers are Claude agents. `GET /jobs/{id}` reads
  the work dir back: quote, metrics, drafts, market band, verdict, price
  candidates, the proposal or its rejection, the log. Stage and status are
  derived from files against `stages.json`, never stored.
  `frontend/src/api/jobs.ts` defines the stage vocabulary (`intake analyze
  slice preview research price design copy publish`). The browser no longer
  calls dapi. Review is a placeholder; approve and catalogue are real since
  2026-09-17.
- **Integrations page (2026-10-13).** `/dapilot:config` runs
  `integrate.py serve`: a stdlib HTTP server on 127.0.0.1 that opens one page
  listing the current integrations (3DAPI, Etsy, fal, Claude for headless
  workers, Instagram API as optional), the steps to obtain each value on the
  platform's side, and one field per value. The browser posts values to the
  server; the server writes `store/.env` in place (mode 0600, other lines
  kept), runs one free verification per integration (`/v1/me`, Etsy
  `openapi-ping` plus `/users/me` through the saved token for the shop name,
  fal's storage token call, Anthropic `/v1/models`, Graph `/me`), and writes
  `store/integrations.json` with ok, summary and which fields verified, never
  a value. Etsy has an Authorize shop button that runs the existing
  `etsy_auth.py login`. The page is served for 30 idle minutes or until
  Finish. `integrate.py status` prints the table; exit 1 while a required
  integration is not connected. The plugin's first hook, `hooks/hooks.json`
  with `guard_secrets.py`, refuses Read, Edit, Write, Grep, Glob and Bash on
  the env file, `etsy_token*.json` and `social/sessions|profiles`, allowing
  the path only as `--env <file>`. Decided after `docs/integrations-plan.md`
  (same day): OAuth stays Etsy-only, browser platforms stay in the social
  plan, no dapi-owned apps. Found on the way, not yet fixed: a 2026-09-14
  read-only Etsy token file is tracked in git (`store/etsy_token.read-only.bak.json`,
  since f1264c7), to be revoked and untracked by a person.

- **Integrations page redesigned (2026-10-13).** The page `integrate.py
  serve` renders is now one table, a row per integration, with four columns:
  integration (name, required or optional, one line on what it is for, a
  link that opens the steps as a row underneath), status (dot and phrase,
  the last check's summary and time), values (one input per env key with
  saved or verified and remove) and actions (Save and test, Test, Authorize
  shop for Etsy). It uses only 3dapi's palette from `../3dapi/frontend/
  tailwind.config.ts`: navy `#1c3254` and deep navy `#142540` for text,
  buttons and the connected dot, light blue `#8eb0e2` for saved-not-tested
  and the focus ring, glacier 50 to 400 for surfaces and borders, ink
  `#080b10` for body text, rust `#813a39` only for a failed check. Inter
  body and Montserrat heading from Google Fonts with system fallbacks, no
  dark mode. Below 820 px the table stacks each row into a block. Checked
  with headless Chrome at 1280 px and in a 390 px frame. Server, API and
  state are unchanged.
- **Quickstart and the brand name (2026-10-13).** `/dapilot:quickstart`
  (`commands/quickstart.md`, `scripts/quickstart.py`): from a checkout it
  runs `shop.sh setup` without a terminal (installs what it can, creates the
  store files, skips every sign-in), prints the 3DAPI wordmark
  (`integrate.py banner`, navy on a terminal), opens the Integrations page on
  port 3106 and prints the connection table. The command tells the model to
  show the script's output as it is and say almost nothing else. The page
  carries the logo (`assets/3dapi-logo.png`, served at `/logo.png`) over the
  table layout it got the same day. The lowercase `dapi` was replaced by
  `3DAPI` in prose across the plugin, docs, CLAUDE.md, the env template and
  the dev scripts (150 places, 40 files); identifiers, file names, env
  variables, URLs and the generated OpenAPI snapshot are untouched. Commands
  are a `commands/` file because Cowork offers typed slash commands only from
  there. The `integrate` skill was removed the same day: chat lists a skill
  and a command of one name as two entries.
- **One store per machine (2026-10-14).** A Code-tab session runs in a
  worktree, where `store/` and `.venv` do not exist, so each one copied a
  fresh env template and the keys looked reset. `scripts/store_path.py` is now
  the single rule: `$DAPILOT_STORE_DIR`, else the checkout's `store/` when it
  holds a `.env` or `config.json`, else `~/.dapilot` (mode 0700). integrate,
  quickstart, the hook, `run_intake.py`, `ledger.py`, `design_product.py`,
  `server/app.py` and `shop.sh` all use it, and the entry points export the
  result to everything they start. `shop.sh` creates its venv under
  `~/.dapilot/venv` when the checkout has none. The hook fences `~/.dapilot`
  as well. On the dev Mac the user settings pin `DAPILOT_STORE_DIR` and
  `DAPILOT_PYTHON` to the checkout. Same day: a server started before an
  update reported the new version by reading `plugin.json` live, so a new
  run reused its old page; the version is now fixed at process start.
- **The plugin carries its templates (2026-10-14).** An install, whether
  uploaded or from a marketplace, copies only `plugins/dapilot`, so the
  store templates moved from `store/` to `plugins/dapilot/templates/`
  (`.env.example`, `config.example.json`, `pricing-policy.example.json`,
  `etsy-listing.example.json`) and `quickstart.py` seeds a new store from
  them; `run_intake.py`, `ledger.py` and `design_product.py` fall back to
  the template config there. `plugins/dapilot/requirements.txt` holds the
  pipeline's packages and the repo's `requirements.txt` includes it plus the
  server's. `quickstart.py` replaced `quickstart.sh` the same day (standard
  library, runs on Windows too; waits while the page is open and prints the
  table only after it closes), the hook picks `python3` or `python`, and the
  commands pre-approve only `python3 <plugin>/scripts/*`.
- **Environment setup on every system (2026-10-14).** `setup_env.py`,
  standard library: `$DAPILOT_PYTHON` when it works, else a venv under the
  store with `plugins/dapilot/requirements.txt`; Blender, Chrome and ffmpeg
  found at the usual places on macOS, Linux and Windows or installed with
  winget, brew, apt or dnf, each in one line; `DAPILOT_PYTHON`, `BLENDER`
  and `CHROME` written to the store's `.env` so the render and capture
  scripts work off macOS. Elevation is the package manager's own prompt on
  the person's screen; when the script cannot ask it prints the command.
  `quickstart.py` runs it everywhere, then `shop.sh setup` only from a
  checkout on macOS or Linux for the console's packages and the Etsy
  taxonomy. The winget branch is written against the package ids
  (BlenderFoundation.Blender, Google.Chrome, Gyan.FFmpeg) and awaits a run
  on a Windows machine.
- **config opens the page, quickstart checks and starts (2026-10-14).** The
  Integrations page is `/dapilot:config` (`integrate.py serve --wait`: opens
  the page, waits while it is open, prints the table when it closes).
  `/dapilot:quickstart [file or folder]` seeds the store, runs `setup_env.py`
  (and `shop.sh setup` from a checkout on macOS or Linux), verifies every
  account against its platform, prints the table, and stops naming the
  required accounts that are not connected; optional ones such as Instagram
  never block. With a file or folder and everything connected it starts
  `run_intake.py` on it in the background under `runs/quickstart-<time>/`
  and says so; the run stops at the price gate as before. The
  `/dapilot:integrate` name is gone.
- **Releases through GitHub (2026-10-14).** The repository is the
  marketplace (`.claude-plugin/marketplace.json` at the root, plugin at
  `./plugins/dapilot`), so `claude plugin marketplace add 3DAPI-Inc/dapilot`
  and `claude plugin install dapilot@dapilot` install it and `claude plugin
  update dapilot@dapilot` pulls a new version. Because `plugin.json` carries
  a `version`, Claude Code delivers an update only when that string changes.
  `scripts/dev/release.sh [patch|minor|major|X.Y.Z] [--no-push]` bumps it,
  validates both manifests with `--strict`, commits the bump alone (the tree
  must be clean), tags `v<version>` and pushes branch and tag.
  `.github/workflows/plugin.yml` validates both manifests on every push that
  touches the plugin and fails a pull request that changes the plugin without
  moving the version. The README gained the install, update and release
  commands. `skills-phase-zero` was merged into `main` the same day (PR #1),
  so the default branch carries the plugin and an install from
  `3DAPI-Inc/dapilot` works.
- **License and listing fields (2026-10-14).** MIT license, copyright 3DAPI
  Inc., as `plugins/dapilot/LICENSE` (the file the plugin directory reads)
  and a copy at the repo root for GitHub. `plugin.json` gained `homepage`,
  `repository`, `license` and `icon` (`./assets/3dapi-logo.png`), the fields
  Anthropic's directory shows on a listing; `claude plugin validate --strict`
  passes with them. Pre-submission check of the plugin folder: 125 files,
  nothing over 256 KiB, no lockfiles, system files, symlinks or binaries.
  Still needed before a listing can publish: the repo public, and the Etsy
  token rewritten out of history first.
- **Quickstart finds or installs Python (2026-10-14).** `quickstart.py` is
  Python, so a machine without a supported interpreter could never start it.
  `/dapilot:quickstart` now runs `scripts/quickstart.sh`, which looks for a
  CPython 3.10 to 3.13 (the range with wheels for trimesh, scipy and
  networkx): a pinned `DAPILOT_PYTHON`, then `python3.13` down to
  `python3.10`, `python3`, `python`, then Homebrew and python.org folders.
  With none, it installs 3.12 with Homebrew, or `python3` with apt or dnf,
  and looks again; with still none it runs `quickstart.py` on any Python 3
  so the person gets the report, and only with no Python at all does it
  stop with the python.org link. Under Git Bash on Windows it relays to
  `scripts/quickstart.bat`, which does the same with the `py` launcher by
  version, `python`, the winget and python.org install folders, and
  `winget install Python.Python.3.12`. `DAPILOT_BOOTSTRAP_ONLY=1` prints the
  chosen interpreter and stops. Tested on macOS where `python3` is 3.14:
  picks 3.13; with only 3.14 on PATH and no brew, prints the message and
  falls back to 3.14; with no Python on PATH falls back to the system 3.9.
  The `.bat` is reviewed, not run; no Windows machine here. The command's
  allowed tools gained `bash`.
- **Publish mode: live or sandbox (2026-10-14).** `publish.mode` in the
  store's `config.json`. `live` is today's behaviour. `sandbox` runs every
  stage up to and including copy, then `publish_product.py` creates nothing
  on Etsy, posts nothing, marks the product record's storefront `sandbox`
  and writes `published.json` with `sandbox: true` and every step under
  `skipped`, so the stage completes and the ledger stops. Once the mode is
  live a sandbox `published.json` no longer counts as done: running the
  script again publishes the item (the ledger will not re-dispatch it by
  itself; the console or a hand run does). `--dry-run` says which mode it
  is in. The Integrations page gained a Settings tab (`#settings`), first
  setting Publishing with a Live / Sandbox switch written through
  `POST /api/settings` into `config.json`, other keys kept, seeded from the
  template when the store has no config yet. `integrate.py status` ends
  with `Publishing: live|sandbox`, and the JSON carries `settings`. The
  config template now ships `"mode": "sandbox"`, so a fresh store cannot
  list on Etsy until a person flips it; a store without the key stays live.
  Tested: template default sandbox, flip to live and back through the API,
  rejected value, config written with the template's other keys intact,
  status line; publish script in sandbox on a fake work dir, cached rerun,
  and a live dry run that proceeds past the sandbox file.
