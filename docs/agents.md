# Swarm hierarchy and orchestration rules

How work moves through dapilot when more than one agent is involved. Read
`CLAUDE.md` first; this document assumes the three-layer contract (script,
skill, orchestrator) and explains the hierarchy above the skill, the rules
each level obeys, and the mechanisms that keep a run from re-doing work,
running stages out of order, or looping.

## Why a hierarchy at all

A single session that ingests a model, renders it, quotes it, writes copy and
publishes it works once. It stops working the moment there are twenty models:

- Context fills with the wrong things. Render logs for model 3 are noise when
  deciding what to do with model 17.
- Blast radius. One bad judgement mid-conversation, with a live API key in
  scope, can touch every model in the batch.
- Nothing is testable. A skill exercised only as one step inside a long
  session cannot be run on its own against a fixture.
- Nothing stops a rerun. Without a ledger, "did we already quote this" is a
  question the model answers from memory, and memory is the thing that fills
  up.

So: fixed roles, one direction of command, and a ledger on disk that is the
only source of truth about what has been done.

## The five roles

```
 Supervisor (human)
   |  starts runs, clears gates, reads reports, edits skills and config
   v
 Director (one per run)                       skill: run-intake
   |  owns runs/<id>/state.json and the budget; spawns one lead per department
   v
 Lead (one per department per run)            agent: catalogue-lead
   |  runs its stage table through ledger.py; spawns its own workers only
   v
 Worker (one per item per stage, short-lived)  agent: catalogue-worker, or the stage's own
   |                                            (design: product-designer)
   |  runs one skill on one item, writes to work/<hash>/, returns one JSON
   v
 Script (no model)
      reads files and env, does one deterministic thing, prints JSON
```

The director and lead split the old orchestrator role: the director decides
which departments run and when the run ends, the lead decides dispatch order
inside its department. Both are bound to `scripts/ledger.py`, which is the
only writer of `state.json`. The full construction plan, departments and
build order are in `docs/store-plan.md`; the general skills every agent
loads (`hierarchy`, `guardrails`, `output-format`, `work-ledger`) are the
short form of this document.

Command flows down only. Results flow up only, as files and one result line.
No level talks sideways.

| Role | Owns | May | May not |
|---|---|---|---|
| Supervisor | the decision to run; every gate; skill definitions | start and stop runs, approve proposed actions, delete stale outputs to force a redo, edit `SKILL.md` | be needed for a stage to complete unless that stage is gated |
| Director | the run: `ledger.py init`, the budget, the final report | spawn one lead per enabled department, validate the lead's result line, write the report | any domain work, spawning workers, re-running a lead that stopped |
| Lead | its department's loop: `ledger.py next`, `record`, `status`, `report` | dispatch exactly the briefs the ledger returns, in parallel, record every result, stop when `work_left` is false | open a mesh, look at a render, write copy, compute a price, call dapi, change a gate, edit `state.json`, dispatch anything the ledger did not return |
| Worker | one `work/<hash>/` for the duration of one stage | run scripts, read inputs named in the brief, write the stage's named outputs, append to `log.md`, return the result JSON | touch any other work dir, run any stage other than the one in the brief, dispatch anything, retry itself, pass `--confirm` to a script |
| Script | its own stdout and the file it was told to write | read env and args, do one thing, print JSON, exit non-zero on any failure | prompt, guess, fall back to a default host, print a credential |

If a task does not fit one row, it is two tasks.

## The stage table is fixed

Intake is a pipeline with a fixed order. The order is data, not judgement,
and it lives in one place (the orchestrator skill's reference file) in this
form:

| Stage | Skill | Requires | Produces | Gate |
|---|---|---|---|---|
| intake | (orchestrator) | a file | `source.<ext>`, `meta.json` | Auto |
| analyze | `analyze-model` | `source.*` | `analysis.json` | Auto |
| slice + price | `quote-model` | `source.*`, `analysis.json` | `quote.json` | Auto (API spend is free for us; still logged) |
| preview | `preview-model` | `source.*`, `analysis.json` | `renders/draft_00.png` | Auto |
| research | `research-comparables` | `analysis.json`, `quote.json`, `renders/draft_00.png` | `comparables.json`, `viability.json` | Auto |
| price | `propose-price` | `quote.json`, `comparables.json`, `viability.json`, drafts | `price.json` (only when sellable; else `price.rejected.json`) | Auto to write the proposal; the price itself is confirmed at publish |
| design | `design-product` (agent `product-designer`) | `source.*`, `analysis.json`, drafts, `price.json`, `comparables.json` | `design.json` (photos in Etsy order, listing video) | Auto, fal spend capped per item by the script |
| copy | `write-listing` (disabled) | `analysis.json`, `quote.json`, `comparables.json`, `price.json`, `design.json` | `listing.json` | Auto |
| publish | `publish-listing` | `listing.json` | `publish.json` | Propose |

Reordered 2026-09-09: the cheap draft turntable, research and the price
proposal run before the final renders, so an item that cannot sell costs a
slice and eight 320 px frames, never a hero render or a video. `price.json`
exists only for a sellable item, which is what keeps design, copy and
publish from ever running for the rest.

2026-09-13: `design` replaced `render`. The storefront is Etsy, not dapi, so
copy and publish are disabled until they are rebuilt for Etsy. A stage may
name its own worker agent in `stages.json` (`"agent"`); the ledger copies it
into the brief and `run_intake.py` launches that agent instead of the
department's worker. Design workers get `minutes_per_stage.design` (40) of
wall clock because a Wan clip can queue for minutes on fal.

The orchestrator runs a stage for an item when, and only when, every file in
Requires exists and the file in Produces does not. It never reorders, skips,
or invents a stage. Preview and quote can run in parallel with quoting for
the same item because their Requires sets allow it; that is the only
parallelism within one item, and it falls out of the table rather than being
decided at runtime.

Other processes (order triage, fulfilment) get their own stage table when
they are built. Same shape, same rules.

## The ledger

Two kinds of state, both plain files, both gitignored, both written only by
the role that owns them.

**Per item, `work/<sha256 of the model file>/`**, written by workers:

```
work/3f9a.../
  source.stl           copy of the upload
  meta.json            original filename, size, hash, first seen
  analysis.json        analyze-model
  quote.json           quote-model
  renders/             preview-model drafts
  design/, design.json design-product: brief, captures, lifestyle media, photos/, video.mp4
  comparables.json     research-comparables
  listing.json         write-listing
  publish.json         publish-listing: what would be sent, or what was sent
  log.md               one paragraph per stage
  .lock                present while a worker holds the dir (run id, stage, pid, started)
```

Content hash as the key gives idempotency for free. The same model uploaded
twice under two names is processed once. A half-finished folder resumes from
where it stopped because the orchestrator looks at which files exist, not at
what it remembers.

**Per run, `runs/<id>/state.json`**, written by the orchestrator only:

```json
{
  "run": "2026-09-03T10-12-intake",
  "started": "...",
  "budget": {"max_workers": 4, "max_render_workers": 3, "max_wall_minutes": 120, "max_attempts": 2},
  "items": {
    "3f9a...": {
      "file": "shark.3mf",
      "stages": {
        "analyze": {"status": "done",    "attempt": 1, "started": "...", "finished": "...", "summary": "15 bodies, all solid"},
        "quote":   {"status": "running", "attempt": 1, "started": "...", "worker": "..."},
        "render":  {"status": "queued"},
        "publish": {"status": "blocked", "reason": "Propose gate: waiting for supervisor"}
      }
    }
  }
}
```

Status vocabulary is `queued running blocked done failed`, the same as
`frontend/src/api/jobs.ts`. Keep them in sync.

`runs/<id>/report.md` is rewritten at the end of every orchestrator pass and
is what the supervisor reads. One line per item, one line per blocked or
failed stage, and the exact command to clear each Propose gate.

## Rules that prevent reruns, out-of-order work and loops

These are the mechanisms, not aspirations. Each one is checkable by reading
files.

**No rerun**

1. A stage whose Produces file exists is `done`. The orchestrator does not
   dispatch it. A worker that is dispatched anyway (a race, a bug) checks the
   same thing first and returns `cached: true` without running anything.
2. Outputs are never overwritten by an agent. To redo a stage, the supervisor
   deletes its output file. That is the only path to a second run of a stage,
   and it is a human action.
3. `quote-model` additionally records the dapi quote id in `quote.json` and
   checks `GET /quotes` for an existing quote on the same upload before
   creating one, so even a deleted local file does not cause a second slice.

**No out-of-order work**

4. A stage is dispatched only when every Requires file exists and was not
   produced by a stage currently `running` or `failed`. Existence is checked
   at dispatch time, not from the ledger, so a stale ledger cannot cause a
   premature dispatch.
5. One worker per work dir at a time. The worker writes `.lock` on entry and
   removes it on exit. A worker that finds a live `.lock` with a different
   stage returns `ok: false, reason: "locked by <stage>"` and the
   orchestrator requeues it for the next pass. A `.lock` older than
   `max_worker_minutes` is stale: `ledger.py next` removes it with a note,
   and `init --resume` sees through it.
5a. A `running` stage stays `running` until its result is recorded or it
   goes stale. Its output appearing on disk early is expected, not
   completion; the ledger never flips a running stage to done from files.
   Otherwise every record arrives at a done stage and is rejected.
5b. The director kills a worker's whole process group at
   `max_worker_minutes`, not only `claude -p`, and keeps every worker's raw
   stdout and stderr under `runs/<id>/workers/`. A run is readable after the
   fact from the ledger, the report and those files, which is what makes it
   reproducible.
6. Gated stages are never dispatched with `--confirm`. The worker writes the
   proposal and returns `needs_human`. The supervisor runs the confirm command
   from `report.md` by hand. The orchestrator then sees the Produces file and
   moves on.

**No loops**

7. The orchestrator's loop is bounded: it makes at most
   `items x stages x max_attempts` dispatches per run, and every pass must
   change at least one stage status or the run ends with whatever is blocked
   listed in the report. A pass that dispatches nothing and changes nothing
   is the exit condition, not a reason to wait and try again.
8. A failed stage is retried once, with the failure reason in the brief.
   The second failure is final for the run. Nothing retries a Propose stage.
9. Workers never dispatch. The worker agent definition does not include the
   `Agent` tool. A worker that decides it needs help returns `needs_human`.
10. A worker has `maxTurns`. A skill that cannot finish in 25 turns is either
    doing orchestration or debugging, and neither is its job.
11. The run has a wall-clock budget. When it is exceeded, running workers
    finish, nothing new is dispatched, and the report says so.

**No drift**

12. The stage table, the gate level per stage, and the tool list per worker
    are files in the repo. Nothing in a brief, a retry, or a user message
    overrides them at runtime. Changing any of them is a commit.

## Worker brief and result

The brief is what the orchestrator sends. Same shape for every skill:

```
run:      2026-09-03T10-12-intake
item:     3f9a...
workdir:  work/3f9a.../
stage:    design
skill:    design-product
agent:    product-designer
requires: source.3mf analysis.json renders/draft_00.png price.json comparables.json
produces: design.json
attempt:  1
previous: (only on attempt 2) "still generating at lifestyle_video"
```

The result is the last line the worker prints. JSON, one line, same shape for
every skill:

```json
{"ok": true, "cached": false, "stage": "design", "outputs": ["design.json"], "needs_human": null, "reason": null, "summary": "Cat Fidget Clicker: 7 photos, lifestyle clip, 1.11 USD"}
```

- `ok: false` with `reason` means the stage could not be completed.
- `needs_human` means the stage did its work but the next action is gated, or
  the skill hit a judgement it should not make alone. The string is the
  question, phrased so it can be answered with yes, no, or one value.
- `summary` is one line and goes into `report.md` verbatim.

Anything longer goes into `log.md` in the work dir.

## Guardrails

Every action a skill can take has one of three levels. The level is written
in the skill, per action, and enforced by what tools and flags the worker has.

| Level | Meaning | Examples |
|---|---|---|
| Auto | Do it, log it. | analyse, render, request a quote, poll a quote, write drafts to disk, read orders |
| Propose | Write the full intended action to disk, return `needs_human`, stop. | create a design, set a price, transition an order status, record items made |
| Never | The worker does not have the tool. | pay an order, delete anything, contact a customer |

Enforcement, concretely:

- `plugins/dapilot/scripts/dapi.py` carries a `GATES` table: which mutating
  operations are `auto` (upload a file, create a quote, estimate shipping)
  and which are `propose` (create or pay an order, change an order status,
  record items made). Anything mutating and unlisted is `propose`. A
  `propose` call without `--confirm` prints the request it would send and
  exits 0. Skills never pass `--confirm`. The supervisor does, from the
  command in `report.md`. The table changes by commit, never at runtime.
- A worker's `tools:` list is an allowlist. A stage at level Never for
  payments does not get a script that can pay.
- The API key in `DAPI_API_KEY` is scoped to the run: PRICING for intake,
  ORDER for order triage, a separate print-farm key for fulfilment. A key
  with `FULL` never goes in an agent's environment.

Moving an action from Propose to Auto is a deliberate edit to the skill,
reviewed like any other change, and only after that action has a record of
approved proposals in `runs/` with no reversals. An agent never promotes a
gate at runtime.

## Failure

- Workers fail closed. Missing env, missing input file, script non-zero exit:
  return `ok: false` immediately. Do not improvise around a missing script.
- One retry, with the reason attached, for Auto stages only.
- Partial batches are fine. Eighteen of twenty done and two failed is a
  successful run with two lines in the report.

## Concurrency and budget

- Analysis is cheap. Fan out to `max_workers`.
- Rendering is CPU bound and slow (about 6 minutes per turntable on the
  reference laptop). `max_render_workers` is physical cores minus one.
  Prefer draft renders (256px, 16 samples) for anything a skill only needs
  to look at; the full turntable is the last thing that runs.
- Quoting is rate limited (500 per minute per key, and a daily quote cap on
  non-integration accounts). Batch where the API allows.
- Never put an image or a mesh into a brief. Paths only.

## Mapping to Claude Code files

Worker agents live in `plugins/dapilot/agents/`. Start with one generic intake worker
and split only when a stage needs a different tool set.

```markdown
---
name: intake-worker
description: Runs one intake stage on one model, as named in the brief. Dispatched by the orchestrator, never by a user.
tools: Read, Bash, Write
skills: analyze-model, quote-model, render-model, research-comparables, write-listing, publish-listing
model: inherit
maxTurns: 25
---
You run exactly one stage on exactly one item. Read the brief. If the
produces file already exists, return cached:true and stop. Otherwise run the
named skill, write only the named outputs into the work dir, append one
paragraph to log.md, print the result JSON as your last line, and stop. You
do not pick up other items, other stages, or retry.
```

Note there is no `Agent` in `tools:`. That is rule 9.

The orchestrator lives in `plugins/dapilot/skills/run-intake/` with
`disable-model-invocation: true` (a person starts it), `allowed-tools`
including `Agent(dapilot:intake-worker)` (plugin agents are namespaced), and
the stage table in
`reference/stages.md`. Today, before that skill exists, the interactive
session plays the role by hand, following this document.

The loop itself is a candidate for a script (`scripts/run_intake.py`) that
reads the ledger, decides the next dispatches, and shells out to
`claude -p` per worker. That would make routing testable without a model.
Build the skill version first, and move the loop to a script once the stage
table has stopped changing.

## What not to build yet

- No message queue, no database, no service. Files are the queue and the
  ledger until they are demonstrably not enough.
- No hosted runner. The loop must work from a laptop first.
- No automatic gate promotion.
- No cross-run agent memory. Anything worth remembering between runs is a
  fact for `CLAUDE.md` or a skill's reference file.
