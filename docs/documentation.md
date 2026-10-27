# dapilot documentation

dapilot is an AI agent that runs a 3D print shop on the 3DAPI platform. Given
a model file it analyses it, has 3DAPI slice and price it, reads the Etsy
market it would compete in, proposes a sale price, and, once a person approves
that price, photographs it, writes the listing and publishes it. One decision
is a person's: the price. Everything else is the agent's.

There are two ways to use it, and this document has one part for each.

| | Part 1: the Claude plugin | Part 2: the platform integration |
|---|---|---|
| Where it runs | your own computer, inside Claude Code | 3DAPI's servers, at dapilot.dapi.digital |
| What you install | Claude Code and the plugin | nothing |
| Where the keys live | on your machine, in a store directory | on the platform, under your dapi.digital account |
| How you approve a price | a question in the chat, or `/dapilot:review` | a button on the dashboard |
| Who pays for the model | you (Claude subscription or API key) | the platform |
| Status (2026-10-23) | released, version 0.1.25 | built and tested on staging; not yet open to sellers |

Both run the same plugin. The platform runs a released copy of it unchanged,
so a listing comes out the same way whichever door you came in by.

---

## Part 1: the Claude plugin

### 1.1 What it is

A Claude Code plugin named `dapilot`, published from the repository
`3DAPI-Inc/dapilot`. It holds:

- **Commands** you type: `/dapilot:quickstart`, `/dapilot:config`,
  `/dapilot:review`.
- **Skills**, one per decision in the pipeline (name the product, pick the
  comparables, choose a candidate price, write the listing).
- **Scripts** that do everything that needs no judgement: geometry, slicing
  through 3DAPI, market statistics, pricing arithmetic, rendering, image
  generation, Etsy calls.
- **Agents** that run one stage on one item, headless, with a turn cap and a
  dollar cap.
- **A hook** that keeps the model's tools away from your credential files,
  and a **monitor** that reports each stage to the chat as it finishes.

The plugin drives 3DAPI only through its documented public API, with an API
key you mint yourself. It needs nothing private to 3DAPI.

### 1.2 What you need

| | Why | Where to get it |
|---|---|---|
| Claude Code | the plugin runs inside it | docs.claude.com, the CLI or the Claude desktop app, signed in once |
| A 3DAPI account and API key | slicing and the cost basis of every price | dapi.digital, API keys. Mint it with the PRICING scope only; add ORDER later for order work. Never use a FULL key |
| An Etsy developer app | market research reads Etsy; publishing writes to your shop | etsy.com/developers/your-apps: the keystring and shared secret. The app must list `http://localhost:3003/oauth/redirect` as a callback |
| A fal.ai key | the four lifestyle photos and the clip in each listing | fal.ai/dashboard/keys. About 2.34 USD per item at list prices |
| Python 3.10 to 3.15, Chrome, ffmpeg | the scripts and the product renders | quickstart finds them or installs them for you |

Instagram is optional and switched off until its browser adapter ships.

How the model is paid for is yours to choose. With nothing set, the headless
workers use whatever `claude auth login` signed the machine in to. A
subscription token from `claude setup-token`, or an `ANTHROPIC_API_KEY`, can
be set in the store's env file instead. Worker spend per item is capped by
stage in the store config: research 1.2, price 0.6, design 5.0, copy 0.8 USD.

### 1.3 Installing

From a terminal:

```bash
claude plugin marketplace add 3DAPI-Inc/dapilot    # the repository is the marketplace
claude plugin install dapilot@dapilot              # the id is the marketplace's name
```

From the Claude desktop app: Customize, Plugins, add `dapilot` from the
`3DAPI-Inc/dapilot` marketplace. Same plugin, same store.

From a checkout, for development: `claude --plugin-dir plugins/dapilot`
loads the folder for one session.

While the repository is private, Claude Code uses your own git credentials
(an SSH key in ssh-agent or an HTTPS credential helper). Without a GitHub SSH
key set `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1`.

### 1.4 First run

Open Claude Code and type:

```
/dapilot:quickstart
```

It prints the 3DAPI wordmark, sets the computer up (a Python it can use, a
venv with the plugin's packages, Chrome and ffmpeg found or installed with
winget, Homebrew, apt or dnf), then checks every account against its platform
with a free request and shows a table of what is connected. If something is
not connected it stops there and names it.

### 1.5 Connecting your accounts

```
/dapilot:config
```

opens the Integrations page in your browser. It is served on your computer
only, at 127.0.0.1. One row per integration (3DAPI, Etsy, fal, Claude,
Instagram) with the steps to obtain each value and a field to paste it in.
Each value is verified against its platform. Nothing you paste passes through
the model: the page writes the store's env file directly, and the plugin's
hook refuses the model's tools on that file afterwards.

The page has a second tab, **Settings**, with one switch:

- **Sandbox** (the default for a new store): every stage runs, including
  design and copy, but nothing is created on Etsy and nothing is posted.
- **Live**: an approved price creates the Etsy listing, uploads the media,
  activates it (0.20 USD on Etsy) and posts to every enabled social platform.

`/dapilot:config status` prints the table without opening the page.

### 1.6 Listing a model

Drop a model file into the chat, or type its path. The plugin's prompt hook
recognises `.stl`, `.3mf`, `.obj`, `.step` and `.stp` and starts quickstart
on it; you can also say "list this" or "what would this sell for". One file is
one run. A folder is one run over every file in it.

The run goes through eight stages in a fixed order, cheap before expensive.
Nothing paid runs for an item that cannot sell, and nothing after the price
runs until you approve it.

| Stage | What happens | Who does it |
|---|---|---|
| analyze | bodies, watertightness, volume, bounding box | script |
| quote | upload to 3DAPI, slice, poll; the quote is the cost basis | script, read by a worker |
| preview | eight draft frames in headless Chrome, about 3 s | script |
| research | the Etsy aisle the item competes in, a per-unit price band, estimated sales weight, viability | worker |
| price | two candidates (quote plus 50% markup, market median) above the break-even floor; the worker picks one with a rationale | worker, checked by a script |
| **gate** | **you approve the price** | **a person** |
| design | product name, one brief, four studio stills, four lifestyle photos and one clip on fal, reviewed once | worker |
| copy | Etsy title, description, tags, materials, social captions, checked against each platform's limits | worker |
| publish | Etsy draft, media, activate, social posts, or a sandbox record | script |

While it runs, the plugin's monitor reports each stage to the chat as it
finishes: a stage done, a failure, a question, the run ended. A quote takes
20 to 40 seconds of slicing; research and design take a few minutes each.
A run that is quiet is not stuck while a director is alive.

### 1.7 The price gate

When the price is ready, Claude asks one question per item: the file name,
what it should sell for, the break-even floor and the pack size if the item
is sold as a set. The options are the computed candidates, the proposal first
and marked Recommended, each with its margin. You can pick one, type your
own price, or answer Not now.

The floor is enforced by a script, not by the model. A typed price below
break-even (cost from 3DAPI's quote, platform fees, VAT) is refused and you
are asked once more. The model never chooses or alters a price.

If you were not in the chat when the gate opened:

```
/dapilot:review
```

shows everything still waiting for a price and asks the same question.

That one approval authorises the rest: photos, listing text and publishing
run on their own in the mode the Settings tab says. Nothing asks you again.

### 1.8 What the plugin keeps on your machine

Everything lives in one store directory: `~/.dapilot` on an installed plugin,
the checkout's own store folder when you run from a clone, or wherever
`DAPILOT_STORE_DIR` points. One store per machine; it survives plugin updates
and reinstalls.

| In the store | What it is |
|---|---|
| the env file | every credential, written by the Integrations page, mode 0600; the model's tools are refused on it |
| `config.json` | budgets, turn caps, design settings, publish mode, social platforms |
| `integrations.json` | the last verification result per integration |
| the Etsy token file | the Etsy shop sign-in; refused to the model like the env file |
| `reference/pricing-policy.json` | markup, fees, VAT and margin rules the price scripts read |
| `products/` | one record per approved product, carrying the quote id a 3DAPI order needs |
| `work/<sha256>/` | one directory per model file, keyed by content hash: analysis, quote, renders, comparables, price, design, listing, and the approval. The same file is never sliced or rendered twice |
| `runs/<id>/` | one directory per run: the ledger (`state.json`), a report, the director's log and every worker's output |

A stage that fails or stops with a question leaves a marker file in the work
dir, and no run touches that stage again until a person clears it: Try again
on the console's job page, or deleting the marker. A stage that finished but
whose output a check found worth a look (no comparables, a price under its
floor, fewer photos than the standard) gets a review marker instead: the run
goes on, the report lists it under "Done, for review", and `ledger.py retry`
runs the stage again if you want, as long as nothing later has used its
output. Before a run starts, the director checks every connection it will
need (3DAPI, Etsy, fal, the Anthropic key when billing to it) and stops with
the failing one named rather than find out at publish.

### 1.9 Keeping it current

```bash
claude plugin update dapilot@dapilot     # then /reload-plugins or a new session
```

Auto-update is off for third-party marketplaces. Turn it on under `/plugin`,
Marketplaces, dapilot, Enable auto-update, and new versions are applied at
the next launch. Installed copies only update when the plugin's version
number changes.

### 1.10 Running it from a terminal

A checkout of the repository adds a console and a one-terminal driver. None
of this is needed to use the plugin from Claude Code.

```bash
scripts/dev/shop.sh setup                 # a fresh machine: tools, venv, store files, sign-ins, check
scripts/dev/shop.sh run <file|folder>     # intake to the price gate, approve, design to publish
scripts/dev/shop.sh status | approve <hash> [price] | resume
scripts/dev/up.sh                         # the job API on :3100 and the console on :8081

# the headless director alone: routing is code, only workers spend tokens
.venv/bin/python plugins/dapilot/scripts/run_intake.py <folder> [--dry-run]
.venv/bin/python plugins/dapilot/scripts/run_intake.py --resume
.venv/bin/python plugins/dapilot/scripts/approve_price.py pending
```

### 1.11 Limits worth knowing

- Publishing has reached an Etsy draft once (2026-09-21) and has never been
  run live to an activated listing by the pipeline itself. New stores start
  in sandbox for that reason.
- A two-colour 3MF is quoted as one filament. Multicolour cost is not yet
  in the price.
- Items sold as a set are priced per unit times the pack; there are no
  quantity variations on one Etsy listing yet.
- Instagram posting is off. Meta blocked the API two days after the first
  token; the browser adapter that replaces it is not built.
- Order triage, fulfilment and catalogue health are designed, not started.
- The pricing policy template assumes an EU seller with 21% VAT inside every
  sale, which understates net for US buyers. Edit the policy file to match
  your shop.

---

## Part 2: the platform integration (dapilot.dapi.digital)

### 2.1 What it is

The same plugin, run for you by 3DAPI. You sign in with your dapi.digital
account, drop a model file on a web page, and approve the price on the same
page. There is nothing to install and no key to paste: the platform mints
the 3DAPI key, holds the Etsy connection you already made on dapi.digital,
pays for the photos and the model, and keeps your files between runs.

Status on 2026-10-23: the web app, the backend routes and the runner are
built; the staging environment ran its first upload through the queue to a
finished run on 2026-10-21. Production is not yet open to sellers, and the
pricing of the service is an open decision.

### 2.2 The pages

| Page | What it is for |
|---|---|
| `/` | the landing page: a twenty-second film and two doors, "your own integration" (the plugin) and "our integration" (the platform) |
| `/quickstart` | the plugin's setup, step by step, for people who would rather run it themselves |
| `/dashboard` | the workbench: a drop zone and 3D view on the left, the files, a note and the run on the right |
| `/products` | every model you have dropped, newest first, where each stands in the pipeline and the price it was given; opening a row shows its run live |

Staging is `staging.dapilot.dapi.digital` and talks to the staging backend;
production talks to `api.dapi.digital`.

### 2.3 Signing in

The platform uses your dapi.digital session. The cookie is scoped to
`.dapi.digital`, so a sign-in on dapi.digital carries over to the dashboard.
A page that gets a 401 sends you to dapi.digital's login and back.

### 2.4 Listing a model

1. **Drop the files.** `.stl`, `.3mf`, `.obj`, `.step` and `.stp`, up to 50
   files in one run and 512 MB each. STL, 3MF and OBJ are drawn in the
   browser before upload with their size in millimetres; STEP is listed
   without a picture. You can add a note about the object under the drop
   zone; it travels with the run.
2. **Upload.** Each file goes straight from the browser to the platform's
   bucket over a presigned link valid 15 minutes. Model files never pass
   through the backend.
3. **Start.** The platform checks every file arrived at its declared size
   and queues the run. Your runs are serialised: one at a time per seller,
   in the order you started them.
4. **Watch.** The run panel updates live over a server-sent event stream;
   nothing polls. Every stage result the plugin writes appears as an event.

The run's status is one of:

| Status | Meaning |
|---|---|
| Uploading | files on their way to the bucket |
| Queued | the message is on the queue; a runner will take it |
| Running | the plugin's director is working on it |
| Your turn: approve a price | at least one item is at the price gate |
| Needs an answer from you | a stage stopped with a question |
| Failed | a stage failed twice, or the director crashed; the supervisor may already be on it |
| Paused at the spend ceiling | the run hit its budget (15 USD by default) with work left |
| Done | every enabled stage finished |
| Cancelled | you stopped it while it was idle |

### 2.5 The price gate

An item waiting for a price shows the plugin's proposal, its rationale, the
candidates with their margins and the break-even floor. Approve as proposed
or type a price. The approval is handed to the plugin's own gate script on
the runner, which enforces the floor: a price below it is refused, the gate
reopens with the reason, and nothing else changes. After an approval the
run is queued again and continues from design to publish.

One answer per item. The moment you click, the card replaces the buttons
with the price you sent and the run shows Queued; the buttons do not come
back unless the runner refuses that price. A second click, or a different
price, is refused until then. If the page was open in two tabs, the second
tab's click gets the same refusal. Try again and Continue work the same way:
one request per stopped stage, shown as "Retry sent" until the runner has
taken it up or refused it.

What you write under the drop zone travels with the run: the research
stage reads it as your word on what the object is and who buys it, and
uses it to pick the product name and the Etsy aisle when the renders
agree. It never overrides what the renders and the measurements show, and
it is optional.

Each item's card shows what the pipeline has made so far: the preview
drafts as a small turntable (move across the picture to turn the object),
the file's own thumbnail before the drafts exist, then the listing photos
and the clip once design is done. Links last a quarter of an hour and the
page renews them.

### 2.6 When something stops

- **A failed stage** shows Try again. It clears the stage's marker and runs
  the item again in the same run with fresh attempts.
- **A stage done, for review** shows what a check found in its output and a
  Redo button. The run did not wait for you: later stages ran on that
  output. Redo sets the output aside and runs the stage again, and is refused
  with a reason once a later stage has used it. Under each item the card says
  what every stage cost and how often it ran; a stage that ran twice is two
  runs of the same stage, and the events say "run 2".
- **Not started.** When a connection fails the check the director runs
  before every run, the run ends at once with the connection named; reconnect
  it on the Integrations page and start the run again.
- **A blocked stage** (a question for a person) shows Continue, which does
  the same once you have dealt with what it asked.
- **Cancel run** is offered on an idle run (at the gate, failed, needing an
  answer, or paused). A cancelled run is left alone by everyone, including
  the supervisor, until you **Resume** it. A queued or running run cannot be
  cancelled: its message is already in flight.
- **The supervisor.** When a run ends with a failed stage or a stage for
  review, the backend sends one message per such stage to a supervisor that
  runs beside the runner. A review it redoes once for a plain defect, else
  it leaves a note.
  It asks one question, "could the next attempt end differently", and
  answers with a retry or leaves the stage as it is, with a note the
  dashboard shows. Its rules: a missing or refused credential is retried
  only after your configuration changed; a transient upstream error is
  retried after 2, 10 and 60 minutes; a quota is retried once after its
  window; a problem with the file itself or the market is never retried; a
  malformed worker result is retried once with the reason in the brief. A
  question for a person is never retried.

### 2.7 What the platform holds for you

| | Where it comes from | Where it goes |
|---|---|---|
| 3DAPI API key | minted when your first run is created, named "dapilot", scopes PRICING and ORDER. It appears in your API keys list on dapi.digital; revoking it stops dapilot, which the dashboard reports as blocked | written into the seller's store env file on the runner for the duration of a job |
| Etsy shop connection | the Etsy integration you connect under your dapi.digital designer settings, with refresh | the token file the plugin's Etsy scripts read |
| Etsy app keys | the platform's own Etsy app | the store env file |
| fal.ai key | the platform's | the store env file |
| Claude | the platform's Anthropic key; workers bill to the API | the runner's environment, never the store |
| Budget | 15 USD per run, 3 parallel workers, 2 attempts per stage, the same for every seller until plans exist | the store's `config.json` |

The credentials are written fresh for every job and are never copied to
durable storage. The store, work dirs and run ledgers are kept in a private
bucket under your seller id and restored before each job, so a second upload
of the same file is not sliced again and an approval continues the same run.
Uploaded source files are kept 30 days; the rest is kept until a retention
rule is set. The browser reads any of it through the backend over short-lived
links.

The landing page's line "Your keys stay on your machine" is true of the
plugin and not of this door. That copy, and the licence line (the page says
MIT, the repository is AGPL-3.0), are open items.

### 2.8 How it works underneath

```
seller's browser
   |  dapilot.dapi.digital: landing, /dashboard upload, gate, /products
   v
backend (3dapi, Hono under /api)   one row per run; mints the dapilot key; holds credentials
   |  SendMessage                                        ^  events, outcome
   v                                                     |
SQS runs queue (FIFO, group id = seller) + dead-letter    |
   |  ReceiveMessage                                     |
   v                                                     |
runner (runner/, one Coolify container on the EC2 host) -+
   |  pull  s3://<bucket>/sellers/<seller>/{store,work,runs}  ->  /data/dapilot/<seller>
   |  write the store's env and config from the backend's answers
   |  run_intake.py <intake> --run <id>     (or approve_price.py, or ledger.py retry, then the director)
   |  push  /data/dapilot/<seller>  ->  s3   (env file and tokens excluded)
   v
plugin  /opt/dapilot/plugins/dapilot   (the release the image was built from, read-only)
```

The one rule: the plugin stays intact. The runner drives it only through
the surface every local user has, a store directory, environment variables,
the director's command line, its exit code and the files it writes. It never
imports the plugin's Python or edits its ledger.

**Messages** carry ids and keys only, never files:

```json
{ "kind": "run" | "approve" | "retry", "seller": "<uuid>", "run": "<id>",
  "keys": ["<s3 key per uploaded model>"], "item": "<hash>", "stage": "<stage>",
  "price": 12.99, "reason": "<retry only>", "by": "<who asked>" }
```

**Per message the runner:** long-polls one message; extends its visibility
every minute while the job runs; pulls the seller's prefix; fetches
credentials and limits from the backend; downloads the models; runs the
director and posts one event per log line; reads the outcome from the ledger
and the markers and posts it; pushes the prefix back; deletes the message. A
crash before the delete re-delivers the job, which is safe: finished stages
are skipped and the sync is idempotent. Poison messages go to the
dead-letter queue after three receives and the run is marked as needing
someone.

**The supervisor** is a second thread of the same container, woken by a
second, standard queue. It is read-only on the store, asks the backend for
the run before every decision, and acts only through the backend's retry
route or a delayed copy of its own message.

**The AWS stack** is `Dapilot-<env>` in the 3dapi CDK app: the two queues
with their dead-letter queues, the sellers bucket, an IAM user for the
runner and grants for the backend's user. It imports nothing from any other
stack and is deployed with `--exclusively` after reading `cdk diff`.

### 2.9 Routes

Browser routes, session cookie, under `https://api.dapi.digital/api`:

| Route | Does |
|---|---|
| `POST /dapilot/runs` | a run row and one presigned PUT per file |
| `POST /dapilot/runs/:id/start` | verify the uploads, queue the run |
| `GET /dapilot/runs`, `GET /dapilot/runs/:id` | your runs, newest first, fifty at most; one run |
| `GET /dapilot/runs/:id/stream`, `GET /dapilot/runs/:id/events` | live stream; the event list |
| `POST /dapilot/runs/:id/items/:item/approve` | the price gate, optional `price` |
| `POST /dapilot/runs/:id/items/:item/retry` | clear a failed or blocked stage and run it again |
| `POST /dapilot/runs/:id/cancel`, `.../resume` | stop an idle run; open it again |

Runner routes, service token, under `/api/internal/dapilot`:

| Route | Direction | Body |
|---|---|---|
| `GET /sellers/:id/credentials` | to the runner | flat env values, plus the Etsy token when connected |
| `GET /sellers/:id/limits` | to the runner | `budget.usd_per_run`, `max_workers_per_lead`, `max_attempts` |
| `POST /runs/:id/events` | from the runner | `run.started`, `pass.dispatched`, `stage.result`, `gate.answered`, `stage.retried`, `run.ended`, `log` |
| `POST /runs/:id/outcome` | from the runner | status per item per stage, open gates with candidates, markers, the director's summary |
| `GET /runs/:id`, `POST /runs/:id/retry` | the supervisor | the run's stops with their retry counts; a retry with `by: supervisor` |

### 2.10 Operating it

The full procedure is `runner/README.md` and the design is `docs/hosting.md`.
In short:

1. **Stack.** From `3dapi/infrastructure/cdk`: `cdk diff Dapilot-<env>
   --exclusively --context env=<env>`, read it, then deploy the same way.
   Note the queue URLs, the bucket name and the runner user name.
2. **Runner.** Create the runner user's access key in the IAM console and
   paste it into the Coolify resource, which builds `runner/Dockerfile` from
   this repository at a release tag with build argument `DAPILOT_RELEASE`.
   A persistent volume at `/data/dapilot`, about 3 GB of memory, no port and
   no domain. The container's log is the health check: one `ok` per check,
   then `starting the queue consumer`.
3. **Backend.** The same queue URLs, bucket and service token in that
   environment's backend. Unset bucket or queue means the write routes answer
   503 and the dashboard says hosted dapilot is off.

Environment the runner container gets:

```
ANTHROPIC_API_KEY             bills the workers; DAPILOT_BILLING=api is baked into the image
AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY   the <env>-dapilot-runner user
DAPILOT_QUEUE_URL             the runs queue
DAPILOT_SUPERVISE_QUEUE_URL   optional; set, the supervisor runs in the same container
DAPILOT_BUCKET                the sellers bucket
DAPILOT_BACKEND_URL           ends in /api, e.g. https://staging-api.dapi.digital/api
DAPILOT_BACKEND_TOKEN         the runner's service token
```

Environment the backend needs for dapilot:

```
DAPILOT_BUCKET, DAPILOT_QUEUE_URL, DAPILOT_SUPERVISE_QUEUE_URL (optional)
DAPILOT_BACKEND_TOKEN         the same value as the runner's
DAPILOT_PUBLIC_API_URL        optional; defaults by environment
DAPILOT_FAL_KEY               the platform's fal key
DAPILOT_BUDGET_USD_PER_RUN    optional; default 15
ETSY_API_KEY, ETSY_SHARED_SECRET
```

**Updating the plugin in production** is a plugin release
(`scripts/dev/release.sh`), then pointing the Coolify resource at the new
tag and redeploying. Read the changelog first: a release that changes what
a stage writes changes what the dashboard reads.

**Testing without AWS.** The consumer and the supervisor both run on a
laptop with directories standing in for the bucket and the backend
(`consumer.py once --local-bucket --local-backend`, `supervisor.py once`),
and `--director-args=--dry-run` runs the whole loop without spending a
token. `triage.py selftest` checks the supervisor's rules against a table of
real failure reasons.

### 2.11 Open decisions

- **Billing and plans.** The platform pays for the model and for fal today.
  One budget for every seller, from env. What a seller pays is not set.
- **Message granularity.** One message per run today, which matches the
  director's spend ceiling. One per item would give finer retries but move
  the ceiling to the backend.
- **Public API gates on the minted key.** Orders require ten prior quotes on
  a key, and the daily quote cap applies unless the account carries the
  integration flag.
- **Retention** of work dirs for items that never reached an approved price.
- **Resume after the spend ceiling.** A paused run waits for a new upload;
  there is no resume message yet.
- **Stopping a run mid-flight.** Cancel works only on an idle run; a running
  director is not interrupted.
- **Landing page copy**: the keys promise and the licence line, above.

### 2.12 Where the code is

| Piece | Repository and path |
|---|---|
| The plugin | `3DAPI-Inc/dapilot`, `plugins/dapilot/` |
| The runner image, consumer, supervisor and triage | `3DAPI-Inc/dapilot`, `runner/` |
| The design of the hosted tier | `3DAPI-Inc/dapilot`, `docs/hosting.md` |
| The web app | `3dapi`, `frontend/dapilot/` |
| Backend routes, browser side and runner side | `3dapi`, `backend/api/[[...route]]/dapilot.ts` and `dapilot-internal.ts` |
| Tables (`dapilot_runs`, `dapilot_run_events`, `dapilot_sellers`) | `3dapi`, `shared/db/app/dapilot.ts` |
| The AWS stack | `3dapi`, `infrastructure/cdk/lib/dapilot-stack.ts` |
| What lives in 3dapi for dapilot | `3dapi`, `docs/dapilot-platform.md` |
