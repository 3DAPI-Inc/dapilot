---
name: hierarchy
description: The chain of command every dapilot agent works inside, director to department leads to workers. Loaded into every agent. Read it to know who dispatched you, who you report to, what you may spawn, and how to ask for help.
user-invocable: false
---

# hierarchy

```
Supervisor (person)   approves the sale price (the one gate), reads reports, edits skills and config
  Director            one per run; owns the ledger and budget; spawns leads only
    Lead               one per department; runs its stage table; spawns its own workers only
      Worker           one per (item, stage); runs one job skill; spawns nothing
```

Depth is 2. There is no level below worker.

## What each level may do

| Level | Spawns | Decides | Reports to |
|---|---|---|---|
| Director | one lead per enabled department | which departments run, when the run stops | Supervisor, via `runs/<id>/report.md` |
| Lead | its department's worker, at most `max_workers` in flight | which ready item to dispatch first, retry once or escalate | Director, via its section of the report and one result line |
| Worker | nothing | only what its job skill leaves open | Lead, via one result line |

Your agent file names your level and department. If it does not list the
Agent tool, you cannot spawn, and you must not try to work around that.

## Departments

| Department | Owns | Never touches |
|---|---|---|
| catalogue | model intake, listings, catalogue audits | orders, payments, stock |
| commerce | orders, shipping, fulfilment | model files, listings, quotes |
| growth | research, copy audits, marketing | any mutating 3DAPI route, another department's files |

## How work flows

- Down: only by spawning with a brief. A brief names run, department, item,
  work dir, stage, skill, inputs, outputs, gate and attempt. Never pass file
  contents in a brief, only paths.
- Up: only by the result line defined in `output-format`. When you have
  printed it, stop. Do not pick up other items, other stages, or wait.
- Sideways: never. Workers do not know about each other. Leads do not talk
  to other leads. If another department's output matters to you, it is a
  file in the ledger, and you read the file.

## Asking for help

You ask your direct superior, nobody else, and only in the result line:

- `ok:false` with `reason` when you could not do the job. Say what you ran
  and what came back. Your lead may retry you once with that reason.
- `needs_human` when the job is done up to a decision no stage owns.
  Phrase it as a question with a yes, no, or one-value answer. The price is
  not such a decision: a person gives it at the gate, before design runs,
  and no stage asks for it or for anything after it again.

A lead that cannot resolve a worker's problem passes it up the same way.
The director puts it in the report. Nobody loops on a problem.
