# dapilot

An agent that runs a 3D print shop on the dapi platform. dapilot is a set of
Claude Code skills plus a small frontend. The skills are built for dapi's own
shop first and published for other dapi sellers afterwards.

Given model files the agent should list them, price them, photograph them,
keep the catalogue healthy, handle the orders that come in, and drive the
print jobs that fulfil them. A person supervises and approves. The agent does
the work.

Each skill owns one decision in one shop process. An orchestrator routes work
between skills. Deterministic scripts do everything that does not need
judgement. That three-layer split is the contract this repo defines, and every
addition has to fit it.

Read before building anything:

- `docs/agents.md`  the swarm hierarchy: roles, what each may and may not
  do, how work is handed over, and the rules that stop reruns and loops
- `docs/skills.md`  how a skill is written, tested and judged
- `docs/market-research.md`  how viability and price are decided, what the
  first runs showed, and the deep-market design that comes next

## Where this stands (September 2026)

- **Intake is the first slice** (model file -> listed, priced, photographed
  product). Geometry analysis and rendering exist as scripts. Quoting is
  available through dapi's public API. Listing creation needs a new public
  API route (see "The dapi public API").
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
  on dapi's storefront for now, so `write-listing` and `publish-listing` are
  unavailable for dapi and stay disabled until they are rebuilt for Etsy.
  dapi remains the slicer, the cost basis and the producer.
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
- **Assembly and part colours come from the project file (2026-09-21).** A
  Bambu project places its objects twice: `<build>` is the print layout,
  `<assemble>` in `Metadata/model_settings.config` is the Assembly View, kept
  whenever a designer split one import into objects. The same file names each
  object's extruder and `project_settings.config` the filament colours.
  `strip_3mf.py` removes both, so the server runs `assembly_3mf.py read` on
  the original first and writes `work/<hash>/assembly.json`:
  `required_assembly` (true when the assembled placement differs from the
  print layout; a person or the design brief's `assembled` may flip it),
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
  `write-social-post` skill, the browser adapters, the schedule. Not yet run
  against Instagram: it needs a Meta app and `INSTAGRAM_ACCESS_TOKEN`. Plan in
  `docs/social-plan.md`.
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
  social post, then writes `published.json`. The person's approval of the sale
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
  Shipping is reported unknown until a weight table or dapi's shipping
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
  The record carries the quote `publicId` that `createOrder` needs (dapi
  builds the design and SKU from the quote at order time) and a
  `dapi_listing` block in the shape of dapi's product save, unsent: the
  public API still has no product route. Placing orders from the
  catalogue is not built. Details in `docs/market-research.md` 4.5 and 4.8.
- **Pricing policy decided 2026-09-09**, in `store/reference/pricing-policy.json`
  (private). Cost basis is dapi's quote as charged, commission included and
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
  dapi's quote, which already runs Bambu Studio. Local geometry analysis is
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

## Scope

- **Internal first.** The shop is ours. No multi-tenancy, no seller signup,
  no billing. Do not build for users who do not exist yet.
- **The agent runs locally.** A Claude Code session with this repo as cwd.
  There is no hosted orchestrator, no worker, no VPS, no Managed Agents
  deployment. When something needs to run, a human runs it. Design so that
  moving to a scheduled or hosted runner later changes nothing about the
  skills, only about who invokes them.
- **The skills will be published.** They drive dapi through its documented
  public API and are meant to be usable by anyone holding a dapi API key.
  Write them so a stranger with no access to dapi's internals can run them.

That last point is a hard constraint, not a preference. It means:

- no dapi-internal endpoints, table names, or schema knowledge in a skill
- no imports from the dapi monorepo, no direct database writes from dapilot
- every credential and base URL arrives from env, never a literal
- a skill that only works because it knows something private is a broken skill

## The dapi repo is next door

The dapi monorepo is checked out at `../3dapi`. Its public API is a separate
NestJS service in `../3dapi/public-api/v1` and is the only part of that repo
a skill may depend on. How to use it:

- **Learn the contract from the public API, not the backend.** The
  controllers under `public-api/v1/src/modules/*/presentation/` are the
  source of truth for routes, DTOs and permissions. When the service is
  running it serves a Scalar reference at `/docs` and the raw spec at
  `/openapi.json`. Read those. Do not read `backend/` or `shared/db/` to
  work out how a skill should behave.
- **When a skill needs something the public API does not offer, add a route
  to the public API.** We own dapi, so the fix for a missing capability is a
  new endpoint in `public-api/v1`, built to that project's
  `ARCHITECTURE_RULES.md`, documented to its `API_DOCS.md` standard, and
  scoped to a permission. The skill then calls the route like any other
  caller would, and whatever it creates takes the same path as anything
  created in the platform UI: same validation, same pending approval, same
  admin pricing step. dapilot never has a second, more privileged path.
- **Every dapi call goes through one script**, `plugins/dapilot/scripts/dapi.py`,
  driven by the contract the API serves at `/openapi.json`. It lists
  operations, describes what a request must contain, validates a payload
  and sends it. Skills never assemble HTTP requests in prose, and the
  `dapi-request` skill is the only one that runs it directly.
- **The contract can be generated offline.** `scripts/dev/dump_openapi.sh`
  boots the Nest app with dummy env and writes `openapi.json` without a
  database or AWS. The snapshot in the `dapi-request` eval directory comes
  from it; regenerate it when the public API changes.

## The dapi public API

Two surfaces, selected by the API key's permission scopes. A key carries a
list of permissions; `FULL` is a superuser. Rate limit is per key, 500
requests per 60 seconds, reported in `X-RateLimit-*` headers. `GET /me` is the
health check and returns the key identity and remaining quota.

**Designer surface.** This is our shop: we publish designs and customers order
them.

| Route | Scope | What it does |
|---|---|---|
| `POST /quotes/upload_file` | PRICING | presigned S3 URL; stl/3mf/obj/step/stp up to 250MB, client PUTs directly |
| `POST /quotes` | PRICING | slice (Bambu Studio on ECS Fargate) and price one file |
| `GET /quotes`, `GET /quotes/:publicId`, `GET /quotes/status_check` | PRICING | list, fetch, poll. A DONE quote carries `metrics` (added 2026-09-08): `model_g`, `total_g`, `print_time_s`, `prepare_time_s`, `filament_count`, the slicer measurements the price was computed from. `model_g` is the shipping weight. |
| `GET /designer/orders`, `GET /designer/orders/:orderId` | ORDER | orders customers placed for our designs |
| `POST /designer/orders` | ORDER | create an order from completed quotes |
| `GET /designer/orders/filaments` | ORDER | available filaments |
| `GET /designer/orders/shipping/rates`, `POST /designer/orders/shipping/estimate` | ORDER | shipping |
| `POST /orders/:orderId/pay` | ORDER | pay from wallet balance |

Designer Orders endpoints require account approval. Integration accounts are
exempt from the daily quote limit and get batch pricing.

**Print Farm surface.** Fulfilment. Relevant if our shop also prints.

| Route | Scope | What it does |
|---|---|---|
| `GET /print-farm/orders`, `GET /print-farm/orders/:orderId` | READ_ORDERS | orders allocated to the farm |
| `PATCH /print-farm/orders/:orderId/status` | WRITE_ORDERS | transition an order's status |
| `GET /print-farm/designs`, `/skus`, `/extras`, `/materials` (+ `/:id`) | SKU | read-only catalogue |
| `GET /print-farm/stocks` (+ `/:id`) | READ_STOCK | stock |
| `GET /print-farm/courier-options` | any key | courier list |
| `GET /print-farm/stock-levels`, `PATCH /print-farm/stock-levels/:id/items-made` | READ_STOCK, WRITE_STOCK | stock levels; record items printed |

**Missing, not pursued while the storefront is Etsy (2026-09-13):** a route that creates a design (listing)
from a completed quote plus title, description, category, tags, images and a
proposed price. Nothing in either surface creates a design today; the product
save path in the platform is session-authed. Until that route exists,
`publish-listing` ends by writing a complete listing payload to
`work/<hash>/listing.json` and stopping at a human gate. New designs land at
`approvalStatus: 'pending'` and an admin sets sales price and per-region
manufacture price, so that admin step stays human even after the route exists.

## The contract: three layers

| Layer | What it is | Decides | Runs without a model |
|---|---|---|---|
| Script | A CLI bundled in the plugin under `scripts/`. Input in, JSON out, exit code says whether it worked. | Nothing | Yes, and must be runnable by hand |
| Skill | A `SKILL.md` in `plugins/dapilot/skills/<name>/`. Knows when to call which script, with what, and what the result means. | One decision in one process | No |
| Orchestrator | Whatever session or agent holds the run. Reads state, picks the next skill, dispatches it, escalates. | Routing and escalation only | No |

Rules that follow from the split:

- **Scripts do, skills decide.** Geometry, pricing arithmetic, slicing, image
  generation, API calls are scripts. Naming, categorisation, copy, camera
  choice, failure triage, "is this order odd" are skills. If a model is
  choosing a price, something has gone wrong.
- **The upload path is deterministic.** Intake stages run in a fixed order
  with fixed inputs and outputs. Since 2026-09-09 the order is analyze,
  quote, preview (drafts), research, price, design (stills, lifestyle media, video), copy,
  publish: nothing expensive runs for an item that cannot sell. The model's judgement is confined to the
  content of a stage (which category, which camera), never to which stage
  runs next or whether to run it again.
- **If logic only exists inside a prompt, it cannot be tested and cannot be
  moved.** Every step a skill takes is a script invocation or a judgement
  call, never arithmetic in prose.
- **The orchestrator never does domain work.** It reads results and routes.
- **State lives on disk, keyed by content hash.** Skills talk to each other
  through files under `work/<sha256>/`, never through conversation context.
  The same upload must never be analysed, rendered or quoted twice.
- **Anything that spends money or changes platform state goes through a
  gate.** See the guardrail table in `docs/agents.md`. Today every gate is a
  human.

## Shop processes

Stage names match `frontend/src/api/jobs.ts`. "Skills" lists the intended
skill names so that future work lands under agreed names.

| Process | Trigger | Stages | Skills | API surface | Status |
|---|---|---|---|---|---|
| Intake | new model file | intake, analyze, slice, preview, research, price, design, copy, publish | `dapi-request`, `analyze-model`, `quote-model`, `preview-model`, `research-comparables`, `propose-price`, `design-product`, `write-listing`, `publish-listing` | Designer: quotes. Listing: Etsy, not dapi | `dapi-request`, `analyze-model`, `quote-model`, `preview-model`, `research-comparables`, `propose-price`, `design-product` done; `render-model` retired; `write-listing`, `publish-listing` disabled until rebuilt for Etsy |
| Pricing review | quote returned, or admin approval pending | price | `review-price` | admin approval is in the platform UI, not the API | not started |
| Catalogue health | schedule | research, copy | `audit-listing` | design read routes to be added on the Designer surface | not started |
| Orders | new or changed order | (none yet) | `triage-order` | Designer: `/designer/orders` | not started |
| Fulfilment | order allocated to our farm | (none yet) | `manage-print-job` | Print Farm: orders, status, stock levels | not started; only if our shop prints |
| Customer contact | | | | | out of scope for now |

Never guess a route. If it is not in `public-api/v1`, it does not exist for
us until someone adds it there.

## Layout

```
plugins/
  dapilot/                        the Claude Code plugin: the publishable unit
    .claude-plugin/plugin.json    manifest (validated with `claude plugin validate`)
    README.md                     requirements, env, how to load
    skills/
      hierarchy/ guardrails/ output-format/ work-ledger/   general skills, loaded into every agent
      dapi-request/               universal API client (job skill, shared by departments)
      analyze-model/              first intake stage
      preview-model/              draft turntable, no judgement
      research-comparables/       research stage; reference/platform-fees.json, search-guide.md
      propose-price/              price stage; picks a candidate, the script checks it
      design-product/             design stage; reference/art-direction.md
      render-model/               retired 2026-09-13, kept for reference
      run-intake/                 director skill; reference/stages.json is the intake stage table
    agents/
      catalogue-lead.md           spawns catalogue-worker and product-designer only
      catalogue-worker.md         no Agent tool; one stage on one item
      product-designer.md         worker for the design stage; loads only design-product
    scripts/
      dapi.py                     spec-driven client: spec, ops, describe, validate, call, upload
      ledger.py                   run state: init, next, record, status, report
      validate_result.py          the result-line contract check
      analyze_mesh.py load_3mf.py make_fixtures.py quote.py
      render_model.py render_scene.py   Blender preview drafts (driver, scene)
      design_product.py               design stage: context, check, produce, finalize
      capture_turntable.py            browser studio captures: stills at yaws, turntables, depth
      generate_image.py generate_video.py fal_queue.py   lifestyle photo and clip on fal, never paid twice
      upscale_video.py                Topaz on fal, manual only
      render_turntable.py             older plain turntable, kept for reference
      strip_3mf.py                    foreign 3mf project -> geometry-only 3mf
      assembly_3mf.py                 Bambu assemble block and part colours -> assembly.json, display.ply
      etsy_listing.py                 Etsy draft, media, status, activate for one work dir -> publish.json
      social_post.py social/          social posting: the Integration and Session contracts, instagram_api
      source_thumbnails.py            a 3MF's own plate and top previews -> thumbnails/, at ledger init
      etsy_search.py etsy_auth.py market_stats.py   research stage: Etsy search, OAuth, viability
      pricing.py price_candidates.py  the sale-price formula and the two candidates; --check is the break-even hard floor
      product_record.py               the product record for an approved price; copies, decides nothing
docs/
  agents.md                       roles and orchestration rules
  skills.md                       skill authoring guideline
  store-plan.md                   the construction plan and build order
  market-research.md              research stage, price chain, findings, deep-market design
server/
  app.py                          FastAPI job API for the frontend (:3100)
store/
  config.example.json             committed template
  config.json                     private: budgets, fan-out, model prices, departments (gitignored)
scripts/
  dev/dump_openapi.sh             generate openapi.json from ../3dapi offline
frontend/                         React 19 + Vite 8 + Tailwind 4 + shadcn (scaffold)
fixtures/                 test meshes (gitignored)
work/<sha256>/            per-model outputs, cache keyed on file hash (gitignored)
runs/<id>/                orchestrator state and reports (gitignored)
out/                      ad hoc render output (gitignored)
```

The shark 3MF used as a real-world fixture lives outside the repo at
`../Happy_Shark_3MF.3mf`; `fixtures/` holds the generated meshes and
`make_fixtures.py` will not overwrite them without `--force`.

The plugin is the thing that ships. A skill may only call scripts that live
inside `plugins/dapilot/scripts/`, referenced as
`${CLAUDE_PLUGIN_ROOT}/scripts/<name>`, so that an installed copy works
without this repo. The scripts at repo root are where things are developed
and measured; when a skill starts depending on one, move it into the plugin
and leave a note. External dependencies the plugin cannot bundle (the Python
venv with trimesh, scipy and networkx; a Blender binary) are declared in the
plugin's README and checked for by the scripts at startup.

```bash
claude --plugin-dir plugins/dapilot     # load the plugin in a session
/dapilot:analyze-model                  # skills are namespaced by plugin name
/reload-plugins                         # after editing a SKILL.md mid-session
```

Publishing later means adding `.claude-plugin/marketplace.json` at the repo
root pointing at `./plugins/dapilot`. Nothing about the skills changes.

## Running things

```bash
# Python lives in a venv; the system python is 3.14 and has no trimesh wheels.
.venv/bin/python scripts/make_fixtures.py
.venv/bin/python scripts/analyze_mesh.py fixtures/multi.stl

# Blender is a separate binary, not a pip package here. render_model.py finds it at the
# default macOS path or via BLENDER in store/.env, converts 3mf to ply, and runs the scene.
.venv/bin/python plugins/dapilot/scripts/render_model.py --workdir work/<hash> --mode draft --frames 8
.venv/bin/python plugins/dapilot/scripts/render_model.py --workdir work/<hash> --mode final --angles "35,125,215,305" [--rotate 90,0,0] [--color '#3B7DD8'] [--layer-lines]

# The design stage by hand. Captures need the studio page (npm run dev), Chrome and ffmpeg.
.venv/bin/python plugins/dapilot/scripts/design_product.py context  --workdir work/<hash>
#   write work/<hash>/design/brief.json, then:
.venv/bin/python plugins/dapilot/scripts/design_product.py check    --workdir work/<hash>
.venv/bin/python plugins/dapilot/scripts/design_product.py produce  --workdir work/<hash>   # exit 3: run again
.venv/bin/python plugins/dapilot/scripts/design_product.py finalize --workdir work/<hash> [--reject lifestyle_2 --reason "..."]

cd frontend && npm run dev        # :8081 (8080 is dapi's); starts the job API on :3100 too if nothing answers there
.venv/bin/uvicorn server.app:app --port 3100 --reload   # the job API by hand; Vite then leaves it alone. Routes are served at / and /api
```

The frontend talks only to `server/app.py` on :3100 (Vite proxies `/api`
there). The server holds the keys, strips foreign 3mf projects, and starts a
director per upload; every stage after that is the same pipeline the
headless runner uses. `frontend/src/api/dapi.ts` is kept for its types
only; the temporary browser-to-dapi exception of 2026-09-04 ended on
2026-09-09.

Measured on the reference laptop (4 cores): an 8-frame draft turntable at
320 px/24 samples about 30 s, a 640 px/64-sample hero about 30 s, a 1024 px/
128-sample hero about 60 to 90 s. Latency is the constraint, not cost.

Environment every skill expects:

```
DAPI_API_KEY      scoped API key, never committed, never sent to the browser
DAPI_API_URL      base URL of the public API service (public-api/v1), local or hosted
DAPILOT_STORE_DIR store-private files (Etsy token, fee schedule, taxonomy cache); research stage
ETSY_KEYSTRING, ETSY_SHARED_SECRET   Etsy Open API v3 app, research stage only
FAL_KEY (or FAL_API_KEY)             fal.ai, design stage paid assets only
```

`DAPI_API_URL` is the origin only; the contract's paths already carry `/v1`.
These live in `store/.env` (gitignored) together with `ANTHROPIC_API_KEY`,
which makes every headless run bill to the API instead of a subscription.
`run_intake.py` and the test harness source that file. Skills must fail
loudly and early when a variable they need is missing, not fall back to a
default host.

Measured cost of the `analyze` stage on two small meshes, 2026-09-03:

| Path | Wall time | Cost | Notes |
|---|---|---|---|
| director and lead as model agents (`/dapilot:run-intake`) | 5.4 min | 0.48 to 0.72 USD | one real analysis; the rest is routing turns |
| `run_intake.py`, workers only | 55 s | 0.21 USD | two real analyses in parallel, about 0.10 each, 8 to 10 turns per worker |

The remaining cost is turns per worker, not routing. The next lever is one
wrapper script per stage so a worker needs about three turns instead of ten. Use a key with only the scopes the run needs: PRICING for
intake, ORDER for order triage, a separate print-farm key for fulfilment.

```bash
# everything, locally: checks, job API on :3100, console on :8081, ctrl-c stops both
scripts/dev/up.sh            # --check only verifies; --resume also picks up unfinished work dirs

# an intake run over a folder, headless: director and lead are code, only workers spend tokens
.venv/bin/python plugins/dapilot/scripts/run_intake.py <folder> [--run my-run] [--dry-run]
.venv/bin/python plugins/dapilot/scripts/run_intake.py --resume          # every work dir with a stage left
.venv/bin/python plugins/dapilot/scripts/run_intake.py --items <hash>,<hash>
# the same loop from an interactive session, with model agents as director and lead
claude --plugin-dir plugins/dapilot            # then: /dapilot:run-intake fixtures

# the ledger by hand
.venv/bin/python plugins/dapilot/scripts/ledger.py status --run my-run

# the universal client, against the committed contract snapshot
.venv/bin/python plugins/dapilot/scripts/dapi.py --spec plugins/dapilot/skills/dapi-request/eval/openapi.json ops
# against a running API
DAPI_API_URL=http://localhost:8080 DAPI_API_KEY=... .venv/bin/python plugins/dapilot/scripts/dapi.py call getMe
```

## 3MF

Bambu exports .3mf, so it matters as much as .stl. Three things worth knowing:

**Read it with `scripts/load_3mf.py`, never trimesh.** trimesh builds an XML DOM
and per-vertex Python objects; dapi measured 183s and 6.08 GB peak on an 8.4MB
project, which is why `tools/bulk-slice/mesh_bbox.py` skips .3mf outright. Our
streaming reader did the same job on a larger file in **1.1s at 0.14 GB**.
Blender is not an escape hatch: 4.5 ships stl/obj/ply/fbx/usd/collada/alembic
and has no 3mf importer at all.

**Bambu wraps the geometry in a component.** The mesh sits on one object; a
second, component-only object references it; the build item points at the
second. Resolving direct meshes alone finds nothing, so components have to be
resolved recursively with transforms composed.

**Every 3mf carries rendered previews** at `Metadata/plate_1.png` and
`top_1.png`, put there by Bambu Studio. That is a free thumbnail with no render
cost, available the instant the file is uploaded.

## Things that already bit us

**trimesh needs scipy.** `mesh.split()` raises `no graph engines available!`
without it. The dependency set is `trimesh scipy networkx`, not just trimesh.

**Never load with `process=False` if you need body counts.** Processing merges
duplicate vertices; without it no two triangles share a vertex and `split()`
returns one body per face. On our 8-body fixture: 8 bodies processed, 5,168
unprocessed. dapi's own `tools/bulk-slice/mesh_bbox.py` has this bug and cannot
detect a multi-part STL.

**Watertightness is a per-body property, not a whole-mesh one.** An articulated
print has interlocking parts, so the combined mesh reports
`is_watertight == False` while every individual body is closed. The shark
fixture is 15 of 15 bodies solid with a non-watertight union. Volume must be
summed per body, or a valid model gets no volume at all. Real uploads are
frequently open in ways synthetic fixtures never are.

**An STL has no structure.** No units, no names, no hierarchy, no shared
vertices. "Eight parts to assemble" is invisible in the file and only
recoverable through connected-component analysis. Never ask a model to read an
STL: a 200k-triangle file is ~15M tokens of float triples with zero semantic
content. Convert to numbers and to renders, and reason over those.

**Blender: update the view layer before reading `matrix_world`.** After
setting `obj.location`, `matrix_world` is stale until the depsgraph runs.
Computing the floor offset from it sank the shark into the backdrop and
rendered a lone fin. `bpy.context.view_layer.update()` first.

**Blender: turntable the product, not the camera.** Orbiting the camera
around a fixed cyclorama puts it behind the wall at yaw 180 and renders grey.
Rotating the product on a pivot keeps camera, lights and backdrop fixed, so
every angle is lit identically and the backdrop is always behind the object.

**Blender: scale before centering.** Scaling happens about the object's origin,
which an STL puts wherever the exporter felt like. Centering first and scaling
second shoves the model back off-centre by a model-dependent amount, so framing
varies silently per upload.

**Blender: Cycles only.** EEVEE and Workbench both need a GPU/GL context and
fail headless with no display. Cycles on CPU is slower and is the only one that
works where this has to run. OpenImageDenoise is what makes low sample counts
viable; below ~16 samples it smears surface detail into mush rather than
leaving noise.

**Blender 4.x renamed the STL importer** to `bpy.ops.wm.stl_import`. 3.x only
has `bpy.ops.import_mesh.stl`. The script tries both.

**Frontend toolchain, as of the scaffold:** `create-vite` >=9 resolves its
target relative to cwd and strips a leading slash. `shadcn init -d` means
`--template=next` and will scaffold a Next app; use `-b radix -p nova`.
TypeScript 6 errors on `baseUrl` as deprecated, and `paths` works without it.
The Vite template sets `erasableSyntaxOnly`, which rejects constructor
parameter properties.

## Rules

- **No deletion without a person's approval.** Never delete, overwrite or
  move-and-remove a file you did not create in the current session unless
  the human has approved that specific deletion. Verify a move succeeded
  before removing its source; `git mv -k` on an untracked file exits 0 and
  does nothing. This applies to agents in a run as much as to the session.
- Do not use em dashes in text shown to users.
- No credential ever reaches the browser. Not a dapi API key, not an Anthropic
  key. The browser gets a short-lived token and nothing else. The temporary
  exception of 2026-09-04 (browser calling staging directly with
  `VITE_DAPI_API_KEY`) ended 2026-09-09; `server/app.py` is the only path.
  `VITE_DAPI_*` in `frontend/.env` is unused and can be removed.
- Prefer deterministic code over a model call. Every stage that can be a script
  should be a script.
- Renders, analysis and quotes are cacheable on the content hash of the model
  file. The same upload must not be processed twice.
- A skill never sets a price, publishes a design, or changes an order state
  without passing a gate. Today every gate is a human. Proposing is fine.
  Doing is not.
- One skill, one decision. If a SKILL.md describes two jobs, split it.
- Never guess a dapi route. Read `public-api/v1`. If it is not there, add it
  there.
- A worker never dispatches other workers. Only the orchestrator dispatches.
