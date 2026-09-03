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
  `analyze` stage is enabled in `run-intake/reference/stages.json`. Next is
  Phase 1: `quote-model`.
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
- **The frontend is a scaffold.** `frontend/src/api/jobs.ts` defines the stage
  vocabulary (`intake analyze slice price render research copy publish`) and
  talks to a job API on :3100 that does not exist. Treat the stage names as
  canonical and the rest as a placeholder.

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
| `GET /quotes`, `GET /quotes/:publicId`, `GET /quotes/status_check` | PRICING | list, fetch, poll |
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

**Missing, and needed for intake:** a route that creates a design (listing)
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
  gate.** See the guardrail table in `docs/agents.md`. Today every gate is a
  human.

## Shop processes

Stage names match `frontend/src/api/jobs.ts`. "Skills" lists the intended
skill names so that future work lands under agreed names.

| Process | Trigger | Stages | Skills | API surface | Status |
|---|---|---|---|---|---|
| Intake | new model file | intake, analyze, slice, price, render, research, copy, publish | `dapi-request`, `analyze-model`, `quote-model`, `render-model`, `research-comparables`, `write-listing`, `publish-listing` | Designer: quotes; design creation route to be added | `dapi-request` done; analyze and render scripts exist; the rest not started |
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
      run-intake/                 director skill; reference/stages.json is the intake stage table
    agents/
      catalogue-lead.md           spawns catalogue-worker only
      catalogue-worker.md         no Agent tool; one stage on one item
    scripts/
      dapi.py                     spec-driven client: spec, ops, describe, validate, call, upload
      ledger.py                   run state: init, next, record, status, report
      validate_result.py          the result-line contract check
      analyze_mesh.py load_3mf.py render_turntable.py make_fixtures.py
docs/
  agents.md                       roles and orchestration rules
  skills.md                       skill authoring guideline
  store-plan.md                   the construction plan and build order
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

# Blender is a separate binary, not a pip package here.
BL=/Applications/Blender.app/Contents/MacOS/Blender
$BL --background --python scripts/render_turntable.py -- <model> <outdir> [frames] [res] [samples]

cd frontend && npm run dev        # :8081, 8080 is dapi's
```

Render defaults are `36 512 48`. Measured on an i7-7700HQ: a 36-frame turntable
at 512px/48 samples takes ~6 min, a 1024px/128-sample hero still ~39s, and a
256px/16-sample draft under a second. Latency is the constraint, not cost.

Environment every skill expects:

```
DAPI_API_KEY      scoped API key, never committed, never sent to the browser
DAPI_API_URL      base URL of the public API service (public-api/v1), local or hosted
```

`DAPI_API_URL` is the origin only; the contract's paths already carry `/v1`.
Skills must fail loudly and early when these are missing, not fall back to a
default host. Use a key with only the scopes the run needs: PRICING for
intake, ORDER for order triage, a separate print-farm key for fulfilment.

```bash
# an intake run over a folder (the director; spawns the lead, which spawns workers)
export DAPILOT_PYTHON=$PWD/.venv/bin/python
claude --plugin-dir plugins/dapilot            # then: /dapilot:run-intake fixtures
# headless: claude -p --plugin-dir plugins/dapilot "/dapilot:run-intake fixtures my-run"

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
  key. The browser gets a short-lived token and nothing else.
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
