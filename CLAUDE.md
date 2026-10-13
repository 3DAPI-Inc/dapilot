# dapilot

An agent that runs a 3D print shop on the 3DAPI platform. dapilot is a set of
Claude Code skills, deterministic scripts, a small job server and a browser
console. The skills are built for 3DAPI's own shop first and published for
other 3DAPI sellers afterwards.

Given a model file the agent lists it, prices it, photographs it, keeps the
catalogue healthy, handles the orders that come in, and drives the print jobs
that fulfil them. A person supervises and approves. The agent does the work.

Each skill owns one decision in one shop process. An orchestrator routes work
between skills. Deterministic scripts do everything that does not need
judgement. That three-layer split is the contract this repo defines, and every
addition has to fit it.

Read before building anything:

- `docs/agents.md`  the swarm hierarchy: roles, what each may and may not
  do, how work is handed over, and the rules that stop reruns and loops
- `docs/skills.md`  how a skill is written, tested and judged
- `docs/market-research.md`  how viability and price are decided, what the
  runs showed, and the open decisions
- `docs/changelog.md`  the dated build log, moved out of this file on
  2026-10-13. This file says what is true today; the log says how it got here.

## Where this stands (2026-10-13)

**What is built.** The intake pipeline runs end to end as code: a model file
in, an Etsy listing and social posts out, with one human gate at the price.
Eight stages, all enabled: analyze, quote, preview, research, price, design,
copy, publish. The director (`run_intake.py`), the ledger and the publish
stage are scripts. Research, price, design and copy are Claude workers, one
stage on one item each. The console (`frontend/`, `server/app.py`) uploads,
shows every stage's output, holds the price gate and files approved products
into the catalogue. `scripts/dev/shop.sh` sets a fresh machine up and runs the
whole thing from one terminal. `/dapilot:integrate` (2026-10-13) opens a local
Integrations page, one table in 3dapi's glacier-and-navy palette with a row
per integration and columns for status, values and actions, where a person
pastes each credential (3DAPI, Etsy, fal, Claude, Instagram) with the steps
to obtain it; the page's server writes
`store/.env`, verifies every value against its platform with a free request,
and records the result in `store/integrations.json`. No value passes through
the agent, and the plugin's PreToolUse hook (`scripts/guard_secrets.py`)
refuses the agent's tools on the env file, the Etsy token and `social/`.
Scripts take the file only as `--env <file>`. `/dapilot:quickstart` is the
first-run command: `scripts/quickstart.sh` runs `shop.sh setup` from a checkout,
prints the 3DAPI wordmark, opens the page on port 3106 and shows the table;
its output is written for a person. The brand is written 3DAPI in every
comment and document; `dapi` stays only in identifiers (`dapi.py`,
`DAPI_API_KEY`, `dapi-request`, the plugin name `dapilot`).

**What has actually run.** Fourteen work dirs. Three items reached an
approved price. One, the kumiko coaster, went through design and copy and
reached an Etsy draft listing on 2026-09-21 (listing 4579602669, state
draft, never activated) and one Instagram reel, sent through the Meta API
before Meta blocked it. No item has a `published.json`. The shop has not sold
anything the pipeline produced.

**What is not built or not working.**

- The browser adapters for social posting (`instagram_web` and the rest over
  CloakBrowser) and the `write-social-post` skill. Instagram is disabled in
  config until the adapter exists; `publish_product.py` skips disabled
  platforms.
- A compact plate layout before quoting. The coaster's 386 mm print row gives
  2 per plate, so its full-plate unit cost is inflated.
- Multicolour cost. `strip_3mf.py` drops the extruder assignment, so a
  two-colour file is quoted as one filament.
- `run_intake.py <folder>` does not read Bambu assemblies; only the server
  upload does.
- Quantity variations on one Etsy listing.
- The console has no Etsy draft, media or activate buttons and no Instagram
  sign-in. Review on the job page is a placeholder.
- Order triage, fulfilment and catalogue health: not started.
- The eval fixtures under the research and price skills carry the legacy
  weight formula and need a rerun.
- Uncommitted: the unit-of-sale rework of 2026-10-12 and the Instagram
  change of 2026-09-23 (22 files). `marketing/` and `brag-output-*/` are
  untracked and not ignored.

## Scope

- **Internal first.** The shop is ours. No multi-tenancy, no seller signup,
  no billing. Do not build for users who do not exist yet.
- **The agent runs locally.** A Claude Code session or `run_intake.py` with
  this repo as cwd. There is no hosted orchestrator, no VPS, no Managed Agents
  deployment. When something needs to run, a person runs it. Design so that
  moving to a scheduled or hosted runner later changes nothing about the
  skills, only about who invokes them.
- **The skills will be published.** They drive 3DAPI through its documented
  public API and are meant to be usable by anyone holding a 3DAPI API key.
  Write them so a stranger with no access to 3DAPI's internals can run them.

That last point is a hard constraint, not a preference. It means:

- no dapi-internal endpoints, table names, or schema knowledge in a skill
- no imports from the 3DAPI monorepo, no direct database writes from dapilot
- every credential and base URL arrives from env, never a literal
- a skill that only works because it knows something private is a broken skill

## The intake pipeline

Stage order and inputs are in `plugins/dapilot/skills/run-intake/reference/stages.json`;
`frontend/src/api/jobs.ts` mirrors the vocabulary. A stage runs when every
`requires` file exists in `work/<sha256>/` and no `produces` file does. The
order is cheap before expensive: nothing paid runs for an item that cannot
sell, and nothing after the price runs until a person approves it.

| Stage | Who runs it | Reads | Writes | What it does |
|---|---|---|---|---|
| analyze | worker, `analyze-model` | `source.*` | `analysis.json` | `analyze_mesh.py`: bodies, watertightness, volume, bounding box. A cheap pre-check, not a source of truth |
| quote | worker, `quote-model` | source, analysis | `quote.json` | `quote.py`: upload, create, poll 3DAPI's quote. Named by sha256, never sliced twice, resumable |
| preview | worker, `preview-model` | source, analysis | `renders/draft_00.png` and 7 more | `render_model.py`: 8-frame Blender draft turntable. No judgement |
| research | worker, `research-comparables` | meta, analysis, quote, drafts | `comparables.json`, `viability.json` | `etsy_search.py` fetches the aisle, the model writes relevance rules, `market_stats.py` builds the per-unit band, the offer, fees, margins and the verdict |
| price | worker, `propose-price` | quote, comparables, viability, drafts | `price.json` | `price_candidates.py` computes the candidates and the break-even floor; the model picks one; `--check` refuses anything below the floor |
| gate | a person | `price.json` | `price.approved.json` | Approve or override on the job page (`POST /jobs/{id}/price`, floor enforced). Files the product record and starts the rest |
| design | `product-designer`, `design-product` | source, analysis, drafts, price, approval, comparables | `design.json`, `design/` | The model names the product and writes one brief; `design_product.py` captures stills and makes four lifestyle photos and one clip on fal; the model reviews the contact sheet and rejects what misrepresents the product |
| copy | worker, `write-listing` | design, approval, comparables, quote | `listing.json`, `social/posts.json` | One draft of Etsy title, description, tags, materials and social captions; `listing_copy.py check` validates and splits it |
| publish | script, no worker | listing, design, approval | `published.json`, `publish.json` | `publish_product.py`: Etsy draft, media upload, activate (0.20 USD), then every enabled social post |

**Failure is a file.** A stage that ends failed or blocked gets
`<stage>.failed.json` or `<stage>.blocked.json` in the work dir. The ledger
will not dispatch it in any run until a person clears it (Try again on the job
page, or `rm`). No stage asks a person a question mid-run; copy picks its own
category when research found no comparables.

**Runs are supervised and resumable.** One director per upload;
`runs/<id>/` keeps `state.json`, `report.md`, `director.log` and every
worker's raw output. Workers run in their own process group and are killed at
`max_worker_minutes` (15; design 40, from `minutes_per_stage`). Turn caps and
USD budgets per stage are in `store/config.json`. `--resume` or the console's
Resume button picks up every work dir with a stage left and no live worker.

### Research and price

- **The market read.** Etsy caps search offsets at 5 for our key, so
  `etsy_search.py` pages by price window inside the taxonomy aisle (required:
  without it a 12 mm charm got a 43 USD median), filters to physical listings
  of the right size, reads the variant closest to our size, and scores every
  survivor for relevance (rules the model writes) and market weight.
- **Market weight is estimated units per day**, from the shop's newest 100
  reviews: the shop's review-to-sale ratio turns reviews in a 365-day window
  into a sales rate, split over its active listings with add-one smoothing.
  Records with no estimate fall back to a log formula and `viability.json`
  says so. Relevance only gates at `min_relevance`; it is not multiplied in.
- **The unit of sale is data.** Each listing's pack size is read by regex
  from the variant, the title, then the description (`offer.units`); a "set"
  with no count is null and stays out of the band. The band is per unit. Our
  offer is the modal pack by weight when it holds `offer.adopt_share` (0.5)
  of the weight, else one. The pack is costed at units times the full-plate
  unit price plus the fixed fees once; one Etsy sale of a set is one 3DAPI
  order line of that quantity.
- **Pricing policy** is `store/reference/pricing-policy.json` (private;
  template `store/pricing-policy.example.json`). Cost basis is 3DAPI's quote
  as charged, commission included, at the full-plate unit price from the
  batch curve (the shop buys in batches of about 100 and sells from stock).
  Two candidates reach the model: quote plus 50% markup, and the market
  median; a margin candidate stands in when the band is unreliable or there
  are no comparables. Hard rule, enforced by `price_candidates.py --check`
  for every author of `price.json`: never below break-even (cost, platform
  fees, VAT owed). A person may override above the floor. The policy assumes
  an EU seller with 21% VAT inside every sale, which understates net for US
  buyers.
- **Quote metrics are per unit.** `model_g`, `total_g`, `print_time_s` on a
  DONE quote are the one-copy plate's measurements (fixed in the API on
  2026-09-09; older quotes must be re-sliced). Batch quoting is a per-account
  flag set in 3DAPI's database.

### Design

- Config in `store/config.json` under `design`: four stills at 2048 px from
  the browser studio (`/studio`, headless Chrome), four lifestyle photos
  (`fal-ai/nano-banana-pro/edit`, about 0.15 USD each) from the chosen still
  plus two other sides, one 6 s clip (`minimax/h3-max/image-to-video`, about
  0.96 USD) animated from the lifestyle photo the brief names. A standard
  item is about 1.56 USD against `usd_cap_per_item`. A rejected or failed
  paid asset is remade once with the rejection in its prompt.
- The 240-frame studio turntable is a fallback only (`turntable.when`):
  rendered when fal failed to make the clip. A clip the reviewer rejected
  gets no video at all. Nothing downstream may depend on the turntable.
- Paid requests are written on submit and a rerun attaches (`fal_queue.py`);
  spend is counted from those files. `produce` exits 3 at its deadline so a
  worker's Bash call never outlives 10 minutes; the worker runs it again.
- **Assembly and colours come from the Bambu project file.** `assembly_3mf.py
  read` writes `assembly.json` (`required_assembly` when the Assembly View
  differs from the print layout and the parts touch, `multicolour`, per-part
  transforms, extruder and colour) and `build` writes `display.ply`, which
  both renderers show instead of `source.<ext>`. The slicer, the quote and
  `analyze_mesh.py` still use the source file.

### Publish and social

- The person's approval of the price is the gate for all of it.
  `publish.auto_activate` and `publish.auto_post` in config hand either half
  back to a person; `publish.auto_since` keeps an older approval from going
  public until re-approved.
- `scripts/social/base.py` defines the `Integration` and `Session` contracts;
  `instagram_api.py` is the one adapter and Meta blocked it two days after
  the first token. Config names `instagram_web` (CloakBrowser, not built).
  Browser adapters load from `DAPILOT_SOCIAL_ADAPTER_DIR`, outside the plugin.
  `social_post.py` is the only caller. Plan in `docs/social-plan.md`.

## The 3DAPI repo is next door

The 3DAPI monorepo is checked out at `../3dapi`. Its public API is a separate
NestJS service in `../3dapi/public-api/v1` and is the only part of that repo
a skill may depend on. How to use it:

- **Learn the contract from the public API, not the backend.** The
  controllers under `public-api/v1/src/modules/*/presentation/` are the
  source of truth for routes, DTOs and permissions. When the service is
  running it serves a Scalar reference at `/docs` and the raw spec at
  `/openapi.json`. Read those. Do not read `backend/` or `shared/db/` to
  work out how a skill should behave.
- **When a skill needs something the public API does not offer, add a route
  to the public API.** We own 3DAPI, so the fix for a missing capability is a
  new endpoint in `public-api/v1`, built to that project's
  `ARCHITECTURE_RULES.md`, documented to its `API_DOCS.md` standard, and
  scoped to a permission. The skill then calls the route like any other
  caller would, and whatever it creates takes the same path as anything
  created in the platform UI. dapilot never has a second, more privileged path.
- **Every 3DAPI call goes through one script**, `plugins/dapilot/scripts/dapi.py`,
  driven by the contract the API serves at `/openapi.json`. It lists
  operations, describes what a request must contain, validates a payload
  and sends it. Skills never assemble HTTP requests in prose, and the
  `dapi-request` skill is the only one that runs it directly.
- **The contract can be generated offline.** `scripts/dev/dump_openapi.sh`
  boots the Nest app with dummy env and writes `openapi.json` without a
  database or AWS. The snapshot in the `dapi-request` eval directory comes
  from it; regenerate it when the public API changes.

## The 3DAPI public API

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
| `GET /quotes`, `GET /quotes/:publicId`, `GET /quotes/status_check` | PRICING | list, fetch, poll. A DONE quote carries `metrics`: `model_g`, `total_g`, `print_time_s`, `prepare_time_s`, `filament_count`. `model_g` is the shipping weight |
| `GET /designer/orders`, `GET /designer/orders/:orderId` | ORDER | orders customers placed for our designs |
| `POST /designer/orders` | ORDER | create an order from completed quotes; prices N units off the batch curve in whole plates |
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

**The storefront is Etsy, not dapi.** Nothing in either surface creates a
design, and that route is not pursued while listings live on Etsy. 3DAPI is
the slicer, the cost basis and the producer. The product record written at
price approval carries the quote `publicId` that `createOrder` needs and an
unsent `dapi_listing` block for the day a product route exists.

Never guess a route. If it is not in `public-api/v1`, it does not exist for
us until someone adds it there.

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
  with fixed inputs and outputs. The model's judgement is confined to the
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
  gate.** See the guardrail table in `docs/agents.md`. Today the one gate is
  the price approval; everything after it is authorised by that approval.

## Shop processes

| Process | Trigger | Stages | Skills | API surface | Status |
|---|---|---|---|---|---|
| Intake | new model file | analyze, quote, preview, research, price, design, copy, publish | `analyze-model`, `quote-model`, `preview-model`, `research-comparables`, `propose-price`, `design-product`, `write-listing`; `publish_product.py` | Designer: quotes. Listing: Etsy | built; run to an Etsy draft once, never to `published.json` |
| Pricing review | quote returned, or admin approval pending | price | `review-price` | admin approval is in the platform UI, not the API | not started |
| Catalogue health | schedule | research, copy | `audit-listing` | design read routes to be added on the Designer surface | not started |
| Orders | new or changed order | (none yet) | `triage-order` | Designer: `/designer/orders` | not started |
| Fulfilment | order allocated to our farm | (none yet) | `manage-print-job` | Print Farm: orders, status, stock levels | not started; only if our shop prints |
| Customer contact | | | | | out of scope for now |

## Layout

```
plugins/
  dapilot/                        the Claude Code plugin: the publishable unit
    .claude-plugin/plugin.json    manifest (validated with `claude plugin validate`)
    README.md                     requirements, env, how to load
    skills/
      hierarchy/ guardrails/ output-format/ work-ledger/   general skills, loaded into every agent
      dapi-request/               universal API client (job skill, shared by departments)
      analyze-model/ quote-model/ preview-model/           the first three stages
      research-comparables/       reference/platform-fees.json, search-guide.md
      propose-price/              picks a candidate, the script checks it
      design-product/             reference/art-direction.md
      write-listing/              Etsy text and social captions as one draft
      render-model/               retired 2026-09-13, kept for reference
      run-intake/                 director skill; reference/stages.json is the stage table
    agents/
      catalogue-lead.md           spawns catalogue-worker and product-designer only
      catalogue-worker.md         no Agent tool; one stage on one item
      product-designer.md         worker for the design stage; loads only design-product
    scripts/
      run_intake.py               the headless director: one per upload, workers are claude -p
      ledger.py                   run state: init, next, record, status, report; the only writer
      validate_result.py          the result-line contract check
      dapi.py                     spec-driven client: spec, ops, describe, validate, call, upload
      analyze_mesh.py load_3mf.py make_fixtures.py quote.py strip_3mf.py assembly_3mf.py source_thumbnails.py
      render_model.py render_scene.py render_turntable.py   Blender drafts (render_turntable is older, reference)
      design_product.py capture_turntable.py generate_image.py generate_video.py fal_queue.py upscale_video.py
      etsy_search.py etsy_auth.py market_stats.py pricing.py price_candidates.py product_record.py
      listing_copy.py etsy_listing.py publish_product.py
      social_post.py social/      Integration and Session contracts, instagram_api
docs/
  agents.md skills.md store-plan.md market-research.md social-plan.md quickstart.md changelog.md
server/app.py                     FastAPI job API for the console (:3100)
frontend/                         React 19 + Vite 8 + Tailwind 4 + shadcn console (:8081), /studio capture page
store/
  config.example.json pricing-policy.example.json etsy-listing.example.json   committed templates
  config.json .env reference/ products/ social/ etsy_token*.json              private, gitignored
scripts/
  dev/shop.sh                     setup, check, run, status, approve, resume from one terminal
  dev/up.sh                       checks, job API and console, ctrl-c stops both
  dev/shop_inspect.py test_phase0.sh dump_openapi.sh
  etsy_search.py etsy_auth.py     stale dev copies; the plugin's are the real ones
fixtures/ work/<sha256>/ runs/<id>/ out/ intake/ test-runs/   gitignored state and output
```

The plugin is the thing that ships. A skill may only call scripts that live
inside `plugins/dapilot/scripts/`, referenced as
`${CLAUDE_PLUGIN_ROOT}/scripts/<name>`, so that an installed copy works
without this repo. External dependencies the plugin cannot bundle (the Python
venv with trimesh, scipy and networkx; Blender; Chrome; ffmpeg) are declared
in the plugin's README and checked for by the scripts at startup.
`CLAUDE_PLUGIN_ROOT` is empty inside headless workers; `run_intake.py`
exports it.

```bash
claude --plugin-dir plugins/dapilot     # load the plugin in a session
/dapilot:analyze-model                  # skills are namespaced by plugin name
/reload-plugins                         # after editing a SKILL.md mid-session
```

Publishing later means adding `.claude-plugin/marketplace.json` at the repo
root pointing at `./plugins/dapilot`. Nothing about the skills changes.

## Running things

```bash
scripts/dev/shop.sh setup                      # a fresh machine: tools, .venv, store files, sign-ins, check
scripts/dev/shop.sh run <file|folder>          # intake to the price gate, approve, design to publish
scripts/dev/shop.sh status | approve <hash> [price] | resume
scripts/dev/up.sh                              # job API on :3100 and console on :8081; --check, --resume

# headless intake over a folder: director and ledger are code, only workers spend tokens
.venv/bin/python plugins/dapilot/scripts/run_intake.py <folder> [--run my-run] [--dry-run]
.venv/bin/python plugins/dapilot/scripts/run_intake.py --resume
.venv/bin/python plugins/dapilot/scripts/run_intake.py --items <hash>,<hash>
.venv/bin/python plugins/dapilot/scripts/ledger.py status --run my-run

# the console pieces by hand
cd frontend && npm run dev                               # :8081; starts the job API if :3100 is silent
.venv/bin/uvicorn server.app:app --port 3100 --reload    # routes served at / and /api

# stage scripts by hand. Python lives in .venv: the system 3.14 has no trimesh wheels
.venv/bin/python plugins/dapilot/scripts/analyze_mesh.py fixtures/multi.stl
.venv/bin/python plugins/dapilot/scripts/render_model.py --workdir work/<hash> --mode draft --frames 8
.venv/bin/python plugins/dapilot/scripts/design_product.py context|check|produce|finalize --workdir work/<hash>
.venv/bin/python plugins/dapilot/scripts/price_candidates.py --workdir work/<hash> [--check]
.venv/bin/python plugins/dapilot/scripts/dapi.py --spec plugins/dapilot/skills/dapi-request/eval/openapi.json ops
```

Blender is a binary, found at the default macOS path or via `BLENDER` in
`store/.env`. The design stage needs the studio page served, Chrome and
ffmpeg. Measured on the reference laptop (4 cores): draft turntable about 30
s; slicer 18 to 40 s per file; studio-only design run 7 min and 37 turns.

Environment every skill expects, in `store/.env` (gitignored), which
`run_intake.py`, `server/app.py` and `shop.sh` source:

```
DAPI_API_KEY      scoped API key, never committed, never sent to the browser
DAPI_API_URL      origin of the public API (public-api/v1); the contract's paths carry /v1
DAPILOT_STORE_DIR store-private files (Etsy token, fee schedule, taxonomy cache, products)
ETSY_KEYSTRING, ETSY_SHARED_SECRET   Etsy Open API v3 app; the OAuth token is $DAPILOT_STORE_DIR/etsy_token.json
FAL_KEY (or FAL_API_KEY)             fal.ai, design stage paid assets only
ANTHROPIC_API_KEY                    makes headless runs bill to the API instead of a subscription
```

Skills must fail loudly and early when a variable they need is missing, not
fall back to a default host. Use a key with only the scopes the run needs:
PRICING for intake, ORDER for order triage, a separate print-farm key for
fulfilment.

Cost per item at current caps: research 1.2, price 0.6, design 1.5, copy 0.8
USD of worker budget, about 1.56 USD of fal assets, 0.20 USD to activate on
Etsy. Run ceiling 15 USD. Turns per worker, not routing, is where the money
goes.

## 3MF

Bambu exports .3mf, so it matters as much as .stl.

**Read it with `load_3mf.py`, never trimesh.** trimesh builds an XML DOM and
per-vertex Python objects; 3DAPI measured 183 s and 6.08 GB peak on an 8.4 MB
project. Our streaming reader did the same job on a larger file in 1.1 s at
0.14 GB. Blender is not an escape hatch: 4.5 has no 3mf importer.

**Bambu wraps the geometry in a component.** The mesh sits on one object; a
second, component-only object references it; the build item points at the
second. Components have to be resolved recursively with transforms composed.

**Every 3mf carries rendered previews** at `Metadata/plate_1.png` and
`top_1.png`. `source_thumbnails.py` copies them at ledger init: a free
thumbnail the instant the file is uploaded.

**Foreign projects fail on dapi.** Creality Print, Orca and Prusa project
files carry printer settings Bambu Studio rejects. `strip_3mf.py` keeps the
geometry and thumbnails only; the stripped file slices. The server upload
runs it; `quote.py` by hand does not.

**A Bambu project places its objects twice.** `<build>` is the print layout;
`<assemble>` in `Metadata/model_settings.config` is the Assembly View. The
same file names each object's extruder and `project_settings.config` the
filament colours. `strip_3mf.py` removes both, so `assembly_3mf.py read` runs
on the original first.

## Things that already bit us

**trimesh needs scipy.** `mesh.split()` raises `no graph engines available!`
without it. The dependency set is `trimesh scipy networkx`.

**Never load with `process=False` if you need body counts.** Processing merges
duplicate vertices; without it `split()` returns one body per face. On our
8-body fixture: 8 bodies processed, 5,168 unprocessed.

**Watertightness is a per-body property.** An articulated print has
interlocking parts, so the combined mesh reports `is_watertight == False`
while every body is closed. Volume must be summed per body.

**An STL has no structure.** No units, no names, no hierarchy. Never ask a
model to read one: a 200k-triangle file is about 15M tokens of float triples.
Convert to numbers and renders, and reason over those.

**Blender: update the view layer before reading `matrix_world`**
(`bpy.context.view_layer.update()`), turntable the product and not the camera,
scale before centering, Cycles only (EEVEE and Workbench need a GL context),
and the 4.x STL importer is `bpy.ops.wm.stl_import`.

**Bambu's Assembly View lines never-assembled objects up in a row.** A
filament welder jig came out as a 479 mm row of three specks and lost research
its only size comparable. `required_assembly` now needs every part within 1
mm of another by bounding box, one connected set.

**Descriptions lie about packs.** "Fits 6 glasses" and "set of 6 available"
appear on things sold singly, so the pack parser reads the description only
when the title says set without a count.

**Etsy refuses review offsets past the first page** on shops we do not own,
and search offsets past 5 for our key. Meta blocked the Instagram API two
days after the first token with nothing changed on our side. Plan for every
third party to change under you.

**The geometry scripts were lost once** (2026-09-03) to an unverified move
followed by a delete. Commit scripts early.

**Frontend toolchain:** `create-vite` >= 9 resolves its target relative to
cwd. `shadcn init -d` scaffolds a Next app; use `-b radix -p nova`. TypeScript
6 errors on `baseUrl`; `paths` works without it. `erasableSyntaxOnly` rejects
constructor parameter properties.

## Rules

- **No deletion without a person's approval.** Never delete, overwrite or
  move-and-remove a file you did not create in the current session unless
  the human has approved that specific deletion. Verify a move succeeded
  before removing its source; `git mv -k` on an untracked file exits 0 and
  does nothing. This applies to agents in a run as much as to the session.
- **Check before creating.** `ls` and `git ls-files` before writing any new
  file; a committed script was overwritten once.
- Do not use em dashes in text shown to users.
- No credential ever reaches the browser. `server/app.py` is the only path
  from the console to anything that holds a key.
- Prefer deterministic code over a model call. Every stage that can be a
  script should be a script.
- Renders, analysis and quotes are cacheable on the content hash of the model
  file. The same upload must not be processed twice.
- A skill never sets a price, publishes a design, or changes an order state
  without passing a gate. Proposing is fine. Doing is not.
- One skill, one decision. If a SKILL.md describes two jobs, split it.
- Never guess a 3DAPI route. Read `public-api/v1`. If it is not there, add it
  there.
- A worker never dispatches other workers. Only the orchestrator dispatches.
- When something changes, update the section of this file that describes it
  and add a dated line to `docs/changelog.md`. This file is not a log.
