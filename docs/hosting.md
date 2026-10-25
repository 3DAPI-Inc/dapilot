# Hosting dapilot

Status: **design agreed, partly built, nothing deployed.** Written
2026-10-16 in the 3dapi repository as `docs/dapilot-platform.md`, moved here
on 2026-10-20 when the runner moved into this repository. Built so far: the
runner image (`runner/`, toolchain and plugin, no queue consumer) and, in the
3dapi repository, the web app (`frontend/dapilot`: landing page and
`/dashboard` upload). Nothing below is deployed.

dapilot is a Claude Code plugin that runs a 3D print shop from a model file,
and sellers run it on their own machines. This document is how 3DAPI hosts
the same plugin for sellers who do not, at dapilot.dapi.digital.

## What lives where

| Piece | Repository | Why |
|---|---|---|
| The plugin | this one, `plugins/dapilot` | the thing that ships; unchanged by hosting |
| The runner image and its consumer | this one, `runner/` | released with the plugin, byte for byte: Coolify builds it from this repository at a release tag, which the 3dapi workers directory could not promise |
| The AWS stack: queue, bucket, identities | 3dapi, `infrastructure/cdk` | the backend that sends the messages is deployed from there; the stack stands alone and touches nothing of the slicer's |
| The web app, the backend routes, accounts | 3dapi | dapi.digital's own frontend, backend and Supabase project |

The image is built from this repository at a release tag and pushed to the
registry the stack creates; the stack's service points at that tag. Updating
dapilot in production is a plugin release followed by an image build and a
task definition bump, in that order.

## The one rule

The plugin stays intact. The platform runs a release of it, read-only, and
talks to it only through the surface every local user has: a store
directory, environment variables, the director's command line, its exit code,
and the files it writes. If hosting ever needs the plugin to do something it
cannot do through that surface, the change goes into the plugin as a feature
every user gets. Nothing in `runner/` forks it, patches it, imports its
Python, or edits its ledger.

Why: the plugin is what this repository builds and tests, and self-hosting
users run exactly that. Two implementations of "how a run works" would drift
on the first release.

## How the plugin runs (facts from its code)

- **The director is Python, not a model.** `run_intake.py` is a deterministic
  loop: `ledger.py init` hashes each model into `work/<sha256>/`, then each
  pass asks `ledger.py next` for ready stages, runs them up to three in
  parallel, records each result with its cost, and stops when no work is
  left, the run's spend ceiling (15 USD by default) is hit, or two passes
  change nothing.
- **Script stages spend no tokens.** preview and publish are plain scripts
  run with `--workdir` (preview since 2026-10-20). analyze and quote are
  still workers and are the next two to convert; they have no judgement in
  them either.
- **Judgement stages are one headless Claude each.** research, price, design
  and copy spawn `claude -p --agent dapilot:<worker> --plugin-dir <plugin>
  --dangerously-skip-permissions --max-turns N --max-budget-usd X
  --output-format json '<brief>'`. The worker has Read, Bash and Write, no
  Agent tool, a turn cap, a dollar cap per stage, and a wall-clock cap; it
  runs in its own process group and the group is killed on timeout.
- **Done is checked on disk.** A stage counts as done only when every file in
  its `produces` list exists. The shape of those files is not checked yet.
- **The price gate is a file.** design requires `price.approved.json`.
  Nothing paid runs before it.
- **Failure is bounded and then waits for a person.** A failed or non-JSON
  worker is recorded and re-queued; after `max_attempts` (default 2) the
  ledger writes `<stage>.failed.json` into the work dir and no run touches
  that stage again until the marker is removed. `needs_human` writes
  `<stage>.blocked.json` with the question. A stale `.lock` is reset once
  after its stage's time budget.
- **Where the step is recorded.** `runs/<id>/state.json` (status, attempt and
  reason per item per stage), the markers above, and every worker's full
  stdout and stderr at `runs/<id>/workers/<item>-<stage>-<attempt>.json`.
  Finding which step failed is a field read, never a log grep.
- **Everything persisted is under one root.** `DAPILOT_STORE_DIR` holds the
  store (config, env file, Etsy token), `work/<hash>/` and `runs/<id>/`. The
  ledger writes `state.json` by temp file and rename, and judges locks by
  modification time, so the root must be a real filesystem.
- **Batches are native.** A folder is one run with one item per file.
  Duplicate content collapses to one work dir; files seen before are not
  redone; the spend ceiling pauses a large batch and a second run resumes it.
- **Every render is WebGL in headless Chromium** from the studio page the
  plugin ships in `plugins/dapilot/studio/`, served by the capture script
  itself on a loopback port. No Blender, no console, no node at runtime.
- **Environment the plugin reads** (`docs/quickstart.md`):
  `DAPILOT_STORE_DIR`, `DAPILOT_PYTHON`, `DAPILOT_BILLING`, `CHROME`,
  `DAPILOT_STUDIO_URL` (unset: the bundled page), `DAPILOT_CACHE_DIR`,
  `CLAUDE_PLUGIN_ROOT`, and in the store's env file `DAPI_API_KEY`,
  `DAPI_API_URL`, the Etsy keys, `FAL_KEY`, `ANTHROPIC_API_KEY`.
- **The guard hook** (`scripts/guard_secrets.py`, PreToolUse) refuses the
  workers' tools on the store's env file, the Etsy token and `social/`.

## Architecture

```
seller's browser
   |  dapilot.dapi.digital (3dapi frontend/dapilot): landing, /dashboard upload, gate, runs
   v
backend (3dapi)           one row per run; mints the dapilot API key; holds credentials
   |  SendMessage                                    ^  events (stage, gate, failed, ended)
   v                                                 |
SQS FIFO queue  (message group id = seller id)       |
   |  ReceiveMessage                                 |
   v                                                 |
runner (runner/, one Coolify container)  -----------+
   |  pull s3://<bucket>/<seller>/ -> /data/dapilot/<seller>
   |  DAPILOT_STORE_DIR=/data/dapilot/<seller>  run_intake.py <intake> --run <id> --billing api
   |  push /data/dapilot/<seller> -> s3://<bucket>/<seller>/   (env file and tokens excluded)
   v
plugin  /opt/dapilot/plugins/dapilot  (this release, read-only)
```

The plugin sees a directory and a command. The runner is the only process
that knows S3, SQS, other sellers or the backend exist.

## The runner image (built: `runner/Dockerfile`)

- `python:3.12-slim-bookworm`, with ffmpeg, Chromium and fonts from apt, the
  plugin's packages in a venv (`DAPILOT_PYTHON`), and the Claude Code CLI
  as one native binary at `/usr/local/bin/claude`, pinned by
  `ARG CLAUDE_CODE_VERSION`: the Dockerfile downloads it from Anthropic's
  release bucket and checks it against the sha256 in that version's
  manifest. The installer script is not used (it fetches the newest CLI
  first, then installs the pinned one). Auto-update is off in the image.
- The plugin is `COPY`'d from the checkout the image is built from, so the
  image for tag `vX.Y.Z` runs exactly that release. `ARG DAPILOT_RELEASE`
  records which, for doctor and the run events.
- Root-owned and world-readable only. Workers run with permissions skipped;
  a read-only plugin is what stops one from "fixing" a script mid-run.
- `CHROME` points at a wrapper that adds `--no-sandbox` and
  `--disable-dev-shm-usage`: a container has no user namespaces, and a
  container's `/dev/shm` is small.
- Runs as `runner`, uid 1000. Claude Code refuses the unattended permission
  mode as root and the director starts every worker in that mode.
- `/data/dapilot` is one subdirectory per seller during a run, a persistent
  volume of the Coolify resource; S3 is the durable copy.
- Default command is `doctor.sh --serve`: every tool, the plugin read-only,
  the data directory writable, two frames of a cube rendered through the
  bundled studio page, and then the queue consumer. A failed check exits
  before any message is taken.

## Where it runs (decided 2026-10-21: Coolify, not Fargate)

The runner is a Coolify resource on the EC2 host, next to 3dapi's other
workers, built by Coolify from this repository at a release tag. The first
design put it on ECS Fargate in the slicer's cluster; that stack was deployed
to production once with zero tasks (2026-10-20), and the deploy, run from a
feature branch, also rewrote the slicer's stack and deleted resources the
branch did not know about. Two lessons went into what follows: the AWS stack
for dapilot imports nothing from any other stack, and it is deployed with
`--exclusively` after reading `cdk diff`. The host already pays for itself,
so the runner's marginal cost is zero until it needs its own instance.

## The AWS stack (3dapi `infrastructure/cdk/lib/dapilot-stack.ts`)

A second stack in the 3dapi CDK app, `Dapilot-<env>`, standing alone. The
deploy order and the Coolify settings are in `runner/README.md`.

| Piece | Notes |
|---|---|
| SQS FIFO queue plus dead-letter queue | copy of the slicer queue definitions; group id is the seller id; visibility 5 min, DLQ after 3 |
| S3 bucket | `intake/` expires after 30 days, `sellers/<id>/` kept; private, CORS for the dashboard origin |
| IAM user `<env>-dapilot-runner` | the container's identity: consume the queue, read and write the bucket, nothing else; the access key is made in the console and lives only in Coolify |
| Grants on the backend's existing user | send on the queue, read and write on the bucket, so the Hono backend can queue runs and presign uploads |

No ECR registry (Coolify builds the image), no Secrets Manager (the
Anthropic key and the backend token are Coolify environment variables), no
task or service. Staging and production are the same stack with the
environment name; staging's bucket and queues are destroyable.

## Storage

`DAPILOT_STORE_DIR` must be a Unix filesystem (rename, lock mtimes, Chromium
and ffmpeg writing frames). S3 is an object store and cannot be it, and S3
mounts (s3fs, Mountpoint) break on exactly the rename and mtime the ledger
relies on. So:

- **Local disk during a run.** The seller's directory on the task's disk.
- **S3 between runs.** `s3://<bucket>/<seller>/{store,work,runs}/`, mirroring
  the local layout. Pulled before the director starts, pushed after it exits.
  The exit is the only moment the root is consistent (no worker mid-write,
  no lock held, ledger written last), which is why the sync is at job
  boundaries and never continuous. The local copy on the volume is a cache
  the next job overwrites from S3.
- **Never synced:** the store's env file and the Etsy token. The runner
  writes them fresh per job from the backend's per-seller credentials.
- **The browser reads S3 through the backend** with short-lived presigned
  links, the way the main app serves files. The bucket stays private.
- **EFS is not needed.** It would only replace the sync for several runner
  hosts sharing one live directory, costs about ten times S3, and is only
  reachable inside AWS.
- **Retention is open.** Work dirs of items that never reach an approved
  price are the bulk of the storage; a sweep after N days is the obvious
  rule and nobody has set N.

## The queue

SQS, FIFO, message group id = seller id, content-based deduplication, with a
dead-letter queue after three receives. FIFO grouping gives the per-seller
serialization the plugin's file locks need for free, and lets any number of
sellers run on any number of runners.

Why SQS and not RabbitMQ or Kafka: it is already in the 3dapi repo (clients
in the backend and the public API, queues in CDK), there is no broker to
operate, and the volume is a few hundred messages a day. Kafka is a retained
log for streams several services replay, which this is not. RabbitMQ is the
same model as SQS with a broker to run.

Message body, ids and keys only, never files:

```json
{ "kind": "run" | "approve" | "retry", "seller": "<uuid>", "run": "<id>",
  "keys": ["<s3 key of each uploaded model>"], "item": "<hash>", "stage": "<stage>",
  "reason": "<retry only, optional: why it could end differently>", "by": "<optional: who asked>" }
```

## The consumer (built: `runner/consumer.py`; run on a laptop, never against AWS)

Python with boto3, the same language as everything else in this repository.
`serve` polls the queue; `once --message FILE --local-bucket DIR
--local-backend DIR` handles one message with directories standing in for
S3 and the backend, which is how it was tested on 2026-10-20 with a dry-run
director (`runner/README.md` has the commands). The approve path ran against
the staging queue on 2026-10-21. The retry path is `ledger.py retry` since
2026-10-22: it clears the stage's marker, failed or blocked, queues the stage
in the same run with fresh attempts and the message's reason on the record;
since 2026-10-23 it also redoes a done stage that holds a review marker.
The sync compares content since 2026-10-23 (a file of the same size is
compared by MD5 against the object's ETag; a multipart ETag falls back to
the size), and `push` deletes within one rule: only a path the last sync
saw on both sides (the root's `.sync.json` manifest) that the root no longer
holds, so a marker a retry cleared or outputs a redo set aside do not come
back from S3, and a path the manifest never saw is never deleted. A refused
approve or retry posts the outcome as well as its event, so the backend's
claim is released either way. The director's `preflight`, `for review` and
`stopped` log lines become `run.preflight`, `stage.review` and
`run.stopped` events. Per message:

1. **Receive** by long poll, one message at a time.
2. **Heartbeat.** Extend the message's visibility every minute while the job
   runs. A dead task stops extending and the message reappears.
3. **Prepare.** Pull the seller's prefix from S3. Fetch the seller's
   credentials and plan limits from the backend; write the store's env file
   and `config.json` (`budget.usd_per_run`, `max_workers`, `max_attempts`).
   Download the message's models into the run's intake folder.
4. **Run** the director as a subprocess with `DAPILOT_STORE_DIR` set. Tail
   its stderr (one line per dispatch and per result) and post each as an
   event to the backend: started, stage done, gate reached, stage failed.
5. **Read the outcome** from `state.json`, the markers and the director's
   JSON summary (cost, counts). Post them.
6. **Triage failures** (below).
7. **Push** the store back to S3, excluding the env file and tokens.
8. **Delete** the message. A crash before this re-delivers the job, which is
   safe: finished stages are skipped and the sync is idempotent.
9. **On SIGTERM** (a deploy, a Spot reclaim): stop the director, push, leave
   the message unacknowledged. Needs the director's own SIGTERM handler,
   which is plugin work.

Poison messages land in the dead-letter queue and the backend marks the run
as needing someone, with the last error attached.

An approval is a message: the backend enqueues `approve`, the runner pulls
the seller's root and runs the plugin's `approve_price.py` on the item (the
floor check and `price.approved.json` are the plugin's), then the director
again, which continues from design. The gate is answered once: the backend
marks the item's gate answered on the run row before it sends the message,
in one conditional update, so a second click or a changed price is refused
until the runner has refused the first (2026-10-23; before that the buttons
stayed live for the whole design-to-publish run and a second approve was
sent, which the plugin answered with the first price). A retry is locked the
same way, per marker. The consumer is idempotent on what reaches it
(2026-10-23): an approval already on disk starts nothing, a retry the ledger
refuses starts nothing, and no director is started while
`runs/<id>/director.alive` is fresh on the root; a job waits for the live one
to end. The runner also writes the plugin template's `budget` block and its
`design.lifestyle_images` and `design.lifestyle_video` blocks (the fal
endpoints and their prices) under the plan limits on every job
(`template_release`), so a stage budget or a model a release changed reaches
sellers whose config was seeded earlier (the design worker's 1.50 USD of
2026-10-23 was the old template value, frozen in S3; the clip engine switch
of the same day would have frozen the same way). The seller's palette,
stills and caps are not touched.

## Supervisor (built 2026-10-22: `runner/supervisor.py` and `runner/triage.py`)

A second queue, `<env>-dapilot-supervise` in the 3dapi CDK stack (standard,
not FIFO, so one message can carry its own delay), wakes the supervisor: the
backend sends one message per stage that stopped failed after it has stored
the outcome, so the row is written first and a supervisor that is down loses
nothing a person cannot still see. Blocked stages, a question for a person,
are never sent. A review marker (a done stage whose output
`check_outputs.py` flagged, 2026-10-23) is sent with `marker: review`:
triage's `Review` kind redoes it once for a plain defect (a price under its
own floor, a design with nothing kept) through the same retry route, which
the ledger honours only while nothing later has used the outputs, and
otherwise leaves a note with the problems for the seller. The supervisor runs inside the runner's container: when
`DAPILOT_SUPERVISE_QUEUE_URL` is set, `consumer.serve` starts its loop in a
second thread, so the one Coolify resource does both and the only setup is
that variable (`doctor.sh --supervise` runs it alone, for a laptop or for
splitting the two later). It is read-only on the store and never sends to
the runs queue itself. Before every decision it asks the
backend for the run (`GET /internal/dapilot/runs/<id>`, flat: status and the
stopped stages with the ledger's `retries` and `previous`), so a cancelled
run and a stage that moved on are left alone whatever the message says. It
has two answers and no third: a retry, through
`POST /internal/dapilot/runs/<id>/retry`, which is the same message and
event a seller's Try again sends with `by: supervisor` and the kind as the
ledger's reason; or leave the stage as it is, as a `supervisor.left` event
with the code, the sentence and what the stop waits for. A retry that should
wait (a transient stop) goes back onto the supervise queue as the same body
with `act: "retry"` and `not_before`, in hops of 900 s, the SQS ceiling,
and the run is looked at again when the time comes. There is no escalate,
because a stop it cannot make work is already on the dashboard exactly as
it is.

The question it answers is not "is this bad" but "could the next attempt end
differently". `runner/triage.py` holds that judgement as one class per kind
of stop, each with the code a script may put in its result line (tier one),
the text patterns for results without a code (tier two), and its policy:

- `config.*` (a value missing, a key refused): never on a timer; a retry only
  when the seller's configuration fingerprint moved since the stop.
- `upstream.transient`: retries spaced 2, 10 and 60 minutes apart, then left.
- `upstream.quota`: one retry after the window, then left.
- `content.*` (the file, the market): never; the same bytes fail the same way.
- `worker.*` (malformed result, out of time): one retry with the reason in
  the brief, which is the one retry the hierarchy already allows.
- `runtime.tool_missing`: never; the image is wrong.
- `blocked`: a question a stage asked a person; a retry is not an answer.
- `unknown`: left and logged; the log is where new codes and patterns come from.

The ledger's `retries` count next to an unchanged `previous` is what tells it
a retry proved nothing. `triage.py selftest` runs the fixture table of real
reasons and must pass before a pattern or a policy changes; `doctor.sh
--supervise` runs it before the supervisor starts. The model is not in the
first version; the `unknown` bucket's log says whether it is needed.

The `changed` flag on a message is the backend's, not the supervisor's: the
backend hashes what a config stop depends on (Etsy connected, the dapilot
key, the platform keys present, the public API, its own build) and sends the
open config stops again with the current hash when the backend starts and
when a seller connects an integration. A change made only on the runner is
not seen until the next backend start or a person's Try again.

An agent orchestrator in place of the Python director was considered and
dropped on 2026-10-16, and so was LangGraph on 2026-10-20: both would
re-express the ledger's semantics and the second would need every skill
ported out of Claude Code. The supervisor sits outside the director's loop
for the same reason.

## Plugin work hosting needs first (built 2026-10-23)

These went into the plugin, so local users get them too, and the consumer
reads what they write:

- **Checks on produced files** (`check_outputs.py`, run by `ledger.py
  record`): a hard problem fails the attempt with the field named; a soft
  one leaves the stage done with `<stage>.review.json`, which the outcome
  carries and the backend sends to the supervisor as a `review` stop.
- **A SIGTERM handler in the director**: workers' process groups get the
  signal, their stages are recorded interrupted (attempt not counted), the
  summary says `stopped`, and the next job on the same root resumes.
- **The transcript path in every failed marker** (`worker_log`) and the
  script's `error` code, so triage and the dashboard find the worker's output
  without searching.
- **Preflight**: the director checks every connection the enabled stages need
  before it starts and refuses with the integration named. On the hosted tier
  it is split (2026-10-25): the runner tests the platform's own credentials
  once when its container starts (`consumer.py preflight` from `doctor.sh`,
  values from `GET /internal/dapilot/platform/credentials`) and exits on a
  failure, which fails the deploy; the director then runs with `--preflight
  seller` and checks only the seller's 3DAPI key and, when publish is live,
  their Etsy shop token.

## Accounts and keys

- **Login** is the same Supabase project as dapi.digital. A session is per
  origin (localStorage), so sharing it across the subdomain needs a cookie
  storage adapter scoped to `.dapi.digital` on both apps.
- **The 3DAPI API key.** The platform stores only the hash of a key, so an
  existing key cannot be reused. On first use the backend mints a key named
  for dapilot, scoped to the user, and the raw value goes straight into the
  per-seller secrets the runner writes into the store's env file. It appears
  in the user's API keys list and revoking it stops dapilot. A 401 from the
  public API is treated as blocked, not failed.
- **Two public-API gates would bite a fresh key** and are decisions, not
  defaults: orders require ten prior quotes on the key, and the daily quote
  cap applies unless the user's API profile carries the integration flag.
- **Etsy** can come from the platform's per-user integration settings, with
  refresh, instead of the plugin's own token file: one more value the runner
  writes per job.
- **Secrets stay out of the model's reach** the way the plugin already
  guarantees: in the env file its hook fences, never in a prompt or a
  message. S3 and SQS access is the runner's own IAM user and never enters
  the store.

## What self-hosting users keep

Everything. They run the plugin from Claude Code against a local folder with
their own keys, their own job server and console. None of the above is in
the plugin, and the only thing the platform depends on is the plugin's file
contract (ledger, markers, worker transcripts), which the plugin's own
console depends on too.

## Open decisions

Each of these changes money, a promise, or what a seller is shown. None is
made by the code written so far.

- **Billing.** Our Anthropic key per run (`DAPILOT_BILLING=api` is baked into
  the image) or a seller-supplied key. Sets the plan price and whether a plan
  needs a usage cap. The measured model spend is about 1 USD per item on the
  agent-per-stage build; direct API calls per judgement stage would make it
  about a fifth of that.
- **Keys promise.** The landing page says "Your keys stay on your machine."
  A hosted runner holds Etsy, fal and 3DAPI credentials server-side. The copy
  changes if hosting ships.
- **Licence copy.** The landing page says MIT; this repository is AGPL.
- **Message granularity.** One message per run (matches the director and its
  spend ceiling) or one per item (finer retries, but the ceiling would have
  to move to the backend).
- **Public-API gates** for dapilot-minted keys: the ten-quote order gate and
  the daily quote cap.
- **Retention** of unapproved work dirs in S3.
- **Session sharing** across the subdomain, or separate sign-in.

## Known gaps in the plugin itself

Inherited unchanged by hosting: no item has reached a published listing
through the pipeline yet (one Etsy draft, never activated), the Instagram
adapter is disabled, multicolour files are quoted as one filament, and there
is no quantity variation on a listing. Before a seller pays, our own shop
runs one model through the hosted path to a live listing and a sale.

## The contract (2026-10-25)

Three documents cross the plugin's boundary, each with one JSON Schema under
`plugins/dapilot/schema/` and a `schema_version`: the director's events
(`runs/<run>/events.jsonl`, one per line), the ledger's markers, and the
outcome of a run (`ledger.py outcome`). The runner forwards the events file
and posts the outcome; it reads no work dir and parses no log line. The 3dapi
backend vendors the schemas (`backend/scripts/vendor-dapilot-schemas.sh`) and
refuses a document that breaks them. `docs/state-machine.md` names every
state behind the documents and who may move it.
