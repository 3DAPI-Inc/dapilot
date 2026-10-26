# The state machine

One page for every state a dapilot run can be in, who may move it, and which
file holds the fact. Written 2026-10-25 after a week in which each patch
uncovered three more holes: the states were re-derived in five places with
five sets of rules. This page is the one set. The schemas under
`plugins/dapilot/schema/` encode the documents named here; `ledger.py` is the
only program that reads a work dir to decide any of it.

Four machines, nested: a **stage** of an item, the **markers** that carry a
stage's stop across runs, the **director** that holds a run, and the
**platform's run status** derived from the outcome. The supervisor acts on
the outcome and is described last.

## 1. A stage of an item

Lives in `runs/<run>/state.json` under `items.<sha256>.stages.<stage>`.
Written only by `ledger.py`. Five states.

| State | Meaning |
|---|---|
| `queued` | Not started in this run, or taken up again. `attempt` counts the tries so far; `previous` says how the last one ended |
| `running` | A worker or the director's script holds it. `dispatched` is when |
| `done` | Its `produces` files exist. `summary` says how; `review` lists what a check found, when it did |
| `failed` | Stopped. A `<stage>.failed.json` marker holds it in every run until cleared |
| `blocked` | Stopped on a question for a person. A `<stage>.blocked.json` marker holds it the same way |

Transitions. "By" is the ledger command; nothing else writes `state.json`.

| From | To | By | When | Side effect |
|---|---|---|---|---|
| `queued` | `running` | `next` | every `requires` file matches a glob, no `.lock`, no earlier stage of the item is running or stopped, `attempt` < `max_attempts` | `attempt` + 1; a brief is returned |
| `queued` | `failed` | `next` | `attempt` >= `max_attempts` | failed marker |
| `queued` | `failed` or `blocked` | `refresh` | a marker from another run is on the work dir ("held") | none; `summary` names the run and the marker |
| `running` | `done` | `record` | result `ok`, every `produces` file exists, `check_outputs` finds no hard problem | markers cleared; soft problems write a review marker and `review` |
| `running` | `queued` | `record` | result `ok` but an output is missing or unusable, attempts left | unusable outputs set aside under `.redo/`; `previous` says why |
| `running` | `queued` | `record` | result not `ok`, attempts left | `previous` = the reason |
| `running` | `queued` | `record --interrupted` | the director was stopped while the worker ran | `attempt` - 1: not counted |
| `running` | `failed` | `record` | not `ok`, or unusable, with attempts exhausted | failed marker |
| `running` | `blocked` | `record` | result carries `needs_human` | blocked marker |
| `running` | `queued` or `failed` | `refresh` | `dispatched` older than `max_wall_minutes` | reset once; failed marker when attempts are exhausted |
| any but `running` | `done` | `refresh` | every `produces` file matches a glob | markers cleared: the outputs are the fact |
| `failed` or `blocked` | `queued` | `retry` | a person, the console or the supervisor asks | marker cleared; `attempt` 0; `retries` + 1; `previous` kept |
| `failed` or `blocked` | `queued` | `refresh` | the marker is gone (someone removed it) | `requeue`, noted |
| `done` | `queued` | `retry` | a review marker is present, or `--redo`; and no later stage has outputs | outputs set aside under `.redo/` |

What is not a state: *interrupted* is a transition back to `queued` with the
attempt uncounted. *Held* is `failed` or `blocked` adopted from another run's
marker. *Cached* is `done` whose result said so. *For review* is `done` with
a note.

## 2. Markers

`work/<sha256>/<stage>.<status>.json`, schema `marker.schema.json`, written
and cleared only by the ledger. The work dir is shared by every run of the
same file, so a marker is how a stop outlives the run that hit it.

| Marker | Holds the stage | Carries | Cleared by |
|---|---|---|---|
| `failed` | yes, in every run | `reason`, the attempt, `worker_log`, the script's `error` code | `retry`; the outputs appearing; a person removing it |
| `blocked` | yes, in every run | `question` | the same |
| `review` | no; the stage is done | `problems` from `check_outputs` | `retry` (a redo); the next `record` of the stage |

A run that registers a work dir with a `failed` or `blocked` marker adopts it
and dispatches nothing for that stage. That is the rule that stopped the
shark three days running (2026-10-22 to 25): the marker was right, the thing
it pointed at was a 0-byte file the glob test had taken for an output.

## 3. The director

`run_intake.py`, one per run. Its states are its exit codes and the events
it writes to `runs/<run>/events.jsonl` (schema `event.schema.json`). Every
line is checked against the schema before it is written.

| Phase | Event | Exit |
|---|---|---|
| Refused before registering anything: no department, no `claude`, no billing key, nothing to run, the ledger would not init | `run.refused` with `reason` and an `error` code | 2 |
| Credentials checked (`--preflight all` or `seller`) | `run.preflight` with `ok`, `text`, `checked`, `failed` | 2 when not ok |
| Items registered | `run.started` with `items`, `workers`, budgets | |
| A pass dispatches | `pass.dispatched` with the briefs | |
| A worker or script ends and the ledger records it | `stage.result` with the ledger's `status` (`done`, `queued`, `failed`, `blocked`, or `rejected` when the ledger refused the result) | |
| A done stage has a review marker | `stage.review` with `problems` | |
| Stopped early | `run.stopped` with `cause`: `signal`, `budget` or `stall` | |
| Finished | `run.ended` with `ok`, `counts`, `workers`, `usd`, `seconds` | 0 when no stage failed, else 1 |
| Anything else worth a line | `log` | |

While it runs it rewrites `runs/<run>/director.alive` every 5 seconds; a
reader takes a file younger than 30 seconds as a live run. The stderr log
says the same things in words and is not a contract.

`ledger.py outcome --run <id>` is the run as one document (schema
`outcome.schema.json`): every item's stages, the open gates, every marker,
the media each item has. A run with no state is a valid outcome with
`started` false. It is the only way anything outside the ledger learns the
state of a work dir.

## 4. The platform's run status

`dapilot_runs.status` in the 3dapi backend, derived, never written by the
plugin. The runner posts events and the outcome; the backend derives.

| Status | Set by | When |
|---|---|---|
| `uploading` | the upload route | the row is created |
| `queued` | the start route; a retry or approval | a message is on the queue |
| `running` | the events route | `run.started` arrives |
| `gate` | the outcome route | `gates_open` > 0 |
| `needs_human` | the outcome route | a `blocked` marker on any item |
| `failed` | the outcome route | a `failed` marker on any item, or `director.exit` not 0 or 1 (the reason becomes the row's `error`) |
| `paused` | the outcome route | stages left `queued` or `running` and nothing above |
| `done` | the outcome route | none of the above |
| `cancelled` | the seller | from any idle status; an outcome posted later is stored but the status stays |

Order matters and is `statusFromOutcome`: gate, then blocked, then failed,
then director exit, then paused, then done. Resume re-derives from the stored
outcome. Delete is allowed from everything but `queued` and `running`.

Two claims live on the backend's copy of the outcome and not in the ledger:
`gate.answered` on an item and `retrying` on a marker. They stop a second
click from sending a second message, expire after 20 minutes, and are
released by the runner's refusal or by the next outcome. They are backend
facts about requests in flight, not plugin facts about work.

## 5. The supervisor

Reads the outcome's markers (`failed` and `review`, never `blocked`, which is
a person's), classifies the reason with `triage.py`, and either requests a
retry through the same route a seller's Try again uses or leaves a note. It
is re-sent the stops on every outcome and on resume, and otherwise only when
the seller's configuration fingerprint changed. It never reads a work dir.

## 6. Where each fact lives

| Fact | Authority | File | Readers |
|---|---|---|---|
| A stage's state in a run | ledger | `runs/<run>/state.json` | the ledger only; everyone else through `outcome` |
| A stop that outlives the run | ledger | `work/<hash>/<stage>.<status>.json` | the ledger only; everyone else through `outcome` |
| What happened, in order | director | `runs/<run>/events.jsonl` | the runner (forwards), the backend (stores), the dashboard (renders) |
| The run as a whole | ledger | `outcome` (computed, not stored by the plugin) | the runner (posts), the backend (stores, derives status), the dashboard, the supervisor |
| A director is alive | director | `runs/<run>/director.alive` | the runner, `approve_price.py pending`, the monitor |
| A worker holds an item | worker | `work/<hash>/.lock` | the ledger (dispatch), the worker |
| The seller's turn | ledger | `price.json` or `price.rejected.json` without `price.approved.json` | through `outcome.items[].gate`, with `market` (the band, the cost, the floor, the verdict, similar listings) and `rejected` when no candidate was allowed |
| A request in flight | backend | `dapilot_runs.outcome` (`answered`, `retrying`) | the backend, the dashboard |

Still reading work dirs directly today, to move behind `outcome` in a later
pass: `approve_price.py pending` (the price gate on a laptop),
`server/app.py` (the dev console), `watch_run.py` (the session monitor, which
parses the stderr log and should read `events.jsonl`).

## 7. Proposals, not done

1. **Existence is not validity.** `exists()` in the ledger is a glob. A
   0-byte or unparseable `produces` file should count as absent, with
   `check_outputs` run when a stage is adopted from disk and on every
   `requires` file before dispatch. One function, every reader.
2. **A redo that cascades.** A redo of analyze is refused because quote and
   preview name the analysis as an input. `retry --redo --cascade` would set
   aside the later stages' outputs too, refusing only past a paid stage.
   With a triage rule for "an input is empty or unparseable", the shark
   heals itself.
3. **Held should be explicit.** A stage adopted from another run's marker
   is `failed` with a sentence in `summary`. A `held_by` field (run, marker
   age) lets the dashboard say "failed three days ago in another run" and
   offer the retry there.
4. **One event schema for every source.** The runner's own kinds
   (`gate.answered`, `stage.retried`) and the backend's (`run.cancelled`,
   `supervise.sent`) are outside the plugin's schema. A `source` field and
   their data shapes in the same file would make the dashboard's timeline
   one contract.
5. **Review as a field, not a marker.** It is the one marker that holds
   nothing. It could live only in the stage record and the outcome, and the
   word "marker" would then mean "a stop" everywhere.
6. **Liveness through the ledger.** `director.alive` and `.lock` are read by
   the runner and the gate directly. A `ledger.py alive --run` answer would
   close the last direct reads.
7. **Start again.** A run refused before registering anything has no item
   to retry and Resume only undoes Cancel. The backend should be able to
   re-queue the stored file keys.
