# Store plan: an autonomous dapi shop run by a hierarchy of agents

The construction plan for dapilot. It fixes the structure that keeps agents
from drifting, leaves them free inside it, and keeps the cost per listing
below what a listing earns. `docs/agents.md` and `docs/skills.md` remain the
detailed rules; this document says what to build, in what order, and why.

## 1. Principles

1. **Structure is data, judgement is bounded.** Who may spawn whom, in what
   order stages run, which actions need a person, how much a run may spend:
   all of that is files in the repo, changed by commit. An agent decides only
   inside a stage.
2. **Two kinds of skill.** General skills are loaded into every agent and
   describe the system itself. Job skills are loaded only into the agents
   whose job they are. Access is enforced by the agent file, not by asking.
3. **Scripts do, skills decide, agents route.** Anything countable is a
   script. A model is spent only where a judgement is needed.
4. **Every level checks the level below.** A worker's output is validated by
   a script before its lead accepts it. A lead's report is validated before
   the director accepts it. Out of parameters means rejected, not repaired
   upstream.
5. **Nothing sensitive in a skill.** Skills will be open source. Keys, hosts,
   internal names, our store's numbers live in env and in a private config
   directory outside the plugin.
6. **Cost is a first-class output.** Every run records what it spent and what
   it produced. A process that costs more than it earns is switched off by
   data, not discovered on an invoice.

## 2. Hierarchy

```
 Supervisor (human)
   │  clears gates, reads reports, edits skills and config
   ▼
 Director                       depth 0   one per run
   │  owns the run ledger and budget; chooses which processes run today
   │  may spawn: leads only, at most one per department
   ├──────────────┬──────────────┐
   ▼              ▼              ▼
 Catalogue lead  Commerce lead  Growth lead        depth 1   one per department
   │  owns its stage table; picks ready (item, stage); retries once
   │  may spawn: its own workers only, at most N in flight
   ▼              ▼              ▼
 Workers         Workers        Workers            depth 2   one per (item, stage)
      one job skill, no spawn tool, maxTurns, prints one result line
```

- **Maximum depth is 2.** Workers have no `Agent` tool. Leads have
  `Agent(dapilot:<own-department>-worker)` only. The director has
  `Agent(dapilot:<department>-lead)` for each department. The spawn matrix is
  therefore the `tools:` lines of three agent files, and nothing else.
- **Fan-out is capped in config**, `max_workers_per_lead`, and by the CPU
  bound of renders.
- **Escalation goes one level up, in a fixed shape.** A worker that cannot
  finish returns `needs_human` or `ok:false` with a reason; its lead decides
  retry once, skip, or escalate to the director; the director decides stop
  or continue and puts it in the report for the supervisor. Nobody messages
  sideways. Nobody messages down except by spawning with a brief.
- **Reporting up is the only way work ends.** A worker's last line is the
  result JSON. A lead's last artefact is its department section of
  `report.md`. The director's is the run report.

Departments and what they own:

| Department | Owns | May touch | May not touch |
|---|---|---|---|
| Catalogue | intake: analyse, quote, render, write listing, publish proposal; catalogue audits | `work/<hash>/`, quotes API (PRICING) | orders, payments, stock, anything under another department's work dirs |
| Commerce | orders: triage, shipping estimates, status proposals; fulfilment if we print | orders API (ORDER), print-farm API (farm key), `orders/<id>/` | model files, listings, quotes |
| Growth | market research, comparables, copy audits, later marketing | read-only on listings and `work/*/listing.json`; external research tools | any dapi mutating route, any file another department writes |

Each department has its own API key scoped to its surface. Growth has no
dapi key at all until it needs read access.

## 3. General skills (every agent loads them)

Short, stable, and mostly pointers to scripts. Loaded via the `skills:` line
of every agent file.

| Skill | Says | Enforced by |
|---|---|---|
| `hierarchy` | the diagram above, depth limit, fan-out cap, escalation shape, "report to your superior and stop" | agent `tools:` lines, `maxTurns`, the director's loop |
| `guardrails` | gates (auto, propose, never), forbidden actions, budgets, no-rerun and no-loop rules, secrets handling | `GATES` in `dapi.py`, `--confirm`, per-department keys, ledger checks |
| `output-format` | the result JSON every agent prints, the `log.md` paragraph, the report section shape | `scripts/validate_result.py`, run by the receiving level before acceptance |
| `work-ledger` | where state lives, content-hash keys, "check for output before doing anything" | file existence checks, `.lock`, `state.json` |

Rules for general skills: under 60 lines each, no department knowledge, no
route names, nothing that changes per store. They describe the machine, not
the shop.

## 4. Job skills (loaded only where the job is)

| Department | Job skills | Notes |
|---|---|---|
| Catalogue | `dapi-request`, `analyze-model`, `quote-model`, `render-model`, `write-listing`, `publish-listing`, `audit-listing` | `dapi-request` exists. Quote is the source of truth for printability and price. |
| Commerce | `dapi-request`, `triage-order`, `estimate-shipping`, `propose-order-status`, `manage-print-job` | All mutations are `propose` until a track record exists. |
| Growth | `research-comparables`, `audit-copy`, later `plan-campaign` | No mutating dapi access. Reads listings from disk. |

`dapi-request` is a job skill shared by two departments, not a general one:
Growth agents do not get it. Sharing is by listing it in two agent files.

## 5. Determinism and autonomy, where each lives

| Fixed by data (never decided at runtime) | Free for the agent (inside a stage) |
|---|---|
| stage order per process (`reference/stages.md` per lead) | how to phrase a listing, which category fits, which render frame is the hero |
| spawn matrix and depth (agent files) | which ready item to dispatch first |
| gate per operation (`GATES`) | whether an order looks unusual enough to flag |
| budgets per run and per stage (`store/config.json`) | which of two failure explanations is more likely, for the retry brief |
| result and report shapes (`output-format`) | the wording of a `needs_human` question |
| retry count: one, Auto stages only | what to change on that one retry |

The loop that keeps this stable: a level dispatches only stages whose inputs
exist and whose output does not; a pass that changes nothing ends the run; a
worker whose output already exists returns `cached` and stops. Chaos needs a
way to re-do or re-order work, and there is none.

## 6. Economics

Target: cost per published listing well under the margin of one sale, and
cost per handled order under a few percent of the order. Measured, not
assumed.

Levers, in order of effect:

1. **Model per level.** Workers that format, validate or poll run on the
   cheapest model. Copy and categorisation run on a mid model. Only the
   director and leads run on the strongest model, and they think in short
   turns because their work is routing. Set in each agent's `model:` line.
2. **Scripts instead of turns.** Polling a quote is a script with backoff,
   not an agent waiting. Validation is a script. Rendering is Blender.
3. **Cache by content hash.** The same file is never analysed, quoted or
   rendered twice. Free 3MF previews replace a render wherever a listing
   only needs a thumbnail.
4. **Turn caps.** `maxTurns` on every worker. A worker that needs more is a
   bug report, not a bill.
5. **Draft before final.** Draft renders for anything a model looks at; the
   full turntable runs once, last, only for items that reached `publish`.
   The product video is that turntable encoded (spec in
   `render-model/SKILL.md`); it is required for every listing, so it runs
   for everything that gets published, never for drafts.
6. **Batch the API.** Integration accounts get batch pricing; quotes for a
   folder go in one pass.
7. **Run-level budget.** `store/config.json` sets a spend ceiling per run and
   per item. The director records usage from every spawned agent into
   `runs/<id>/cost.json` and stops dispatching when the ceiling is hit.

Every run report ends with a cost line: items done, tokens by level, wall
time, estimated spend, spend per item. Processes whose spend per item
exceeds the configured threshold for three runs get `enabled: false` in
config until someone looks.

## 7. Open-source boundary

```
plugins/dapilot/          public: skills, agents, scripts, evals. No numbers, no names.
store/                    private, gitignored or separate repo:
  config.json             departments enabled, N, budgets, model choices, thresholds
  .env                    DAPI_API_URL, one DAPI_API_KEY per department
  reference/              our category list, our pricing policy, our copy voice
work/  runs/  orders/     private state
```

Skills read store specifics from `${DAPILOT_STORE_DIR}` and fail loudly if a
file they need is missing. A pre-commit check greps the plugin for key
patterns, hostnames and anything matching the store's private names.

## 8. Build order

Each phase ends with something that runs and a number that was measured.

| Phase | Build | Done when |
|---|---|---|
| 0. Skeleton | four general skills; `validate_result.py`; `catalogue-worker` and `catalogue-lead` agent files; `run-intake` as the director for one department; `store/config.json` | a two-item dry run through the mock server spawns workers, collects two result lines, writes `state.json` and `report.md`, and rejects one deliberately malformed result |
| 1. Quote | `quote-model` on top of `dapi-request`; quote polling script | three fixtures quoted against a real API, no file quoted twice on rerun, cost per quote recorded |
| 2. Listing | `render-model` (stills and the required turntable video), `write-listing`, `publish-listing` (proposal only, refuses a listing with no video); design-create route added in `public-api/v1` | first listing approved by a person from `report.md`, with stills and video attached, cost per listing recorded |
| 3. Commerce | `commerce-lead`, `triage-order`, `estimate-shipping` read-only; then `propose-order-status` | a day of real orders triaged with zero mutations, then one status change proposed and confirmed by hand |
| 4. Growth | `growth-lead`, `research-comparables`, `audit-copy` | comparables attached to listings; audit flags reviewed; no dapi key issued to Growth |
| 5. Autonomy | director spanning all departments; scheduled runs; gates promoted by track record, one at a time, by commit | a scheduled run completes end to end with every Propose cleared by policy, and the cost line stays under threshold |

Phase 0 is small and unblocks everything. Phases 1 and 2 are where the money
question gets answered, so they come before any second department.

## 9. Immediate housekeeping

- `plugins/dapilot/skills/guardrail/agent-guardrails.md` is empty and is not
  a skill until it is `guardrails/SKILL.md` with frontmatter. Its content is
  the `guardrails` row in section 3.
- The geometry scripts under `plugins/scripts/` must move into
  `plugins/dapilot/scripts/` before `analyze-model` or `render-model` exist.
- `docs/agents.md` describes four roles; section 2 above adds the lead level.
  Revise it during Phase 0 so the two documents agree.
