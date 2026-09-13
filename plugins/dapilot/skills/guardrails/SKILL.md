---
name: guardrails
description: The limits every dapilot agent works within, gates on actions, budgets, no reruns, no loops, secrets. Loaded into every agent. If a step would cross one of these, stop and report instead.
user-invocable: false
---

# guardrails

## Gates

Every action is at one of three levels. The level is set per operation in
the repo and never changes at runtime.

| Level | Meaning | Where it is set |
|---|---|---|
| auto | do it, log it | default for reads; listed mutating operations in `GATES` of `scripts/dapi.py` |
| propose | write the exact intended action to the work dir, return `needs_human`, stop | unlisted mutating operations; every stage marked `gate: propose` |
| never | you do not have the tool | anything absent from your agent file |

Never pass `--confirm` to any script. A person does that, from the report.
Never change a gate, a stage table, a budget, or an agent file during a run.

## No deletion

Never delete, overwrite or move-and-remove a file you did not create in
this run. Outputs are deleted only by a person. If a step seems to need a
deletion, return `needs_human` with the path and the reason.

## No rerun

- Before doing anything, check whether your stage's output already exists in
  the work dir. If it does, return `cached:true` and stop.
- Never overwrite an existing output. To redo a stage, a person deletes it.
- Never process the same model twice: work is keyed by the file's content
  hash, and the ledger decides what is ready. You do not.

## No loop

- A worker does one stage on one item, then stops. It never dispatches, never
  retries itself, never waits for something to change.
- A lead dispatches only what `ledger.py next` returns, records every result
  with `ledger.py record`, and ends its loop the moment `ledger.py status`
  reports no work left. Two passes that change nothing means stop.
- A failed stage is retried once, by the lead, with the reason in the brief.
  The second failure is final for the run. Propose stages are never retried.
- Respect `maxTurns`. If you are near it, write what you have, return
  `ok:false` with the reason, and stop.

## Budget

- The run has a spend ceiling in `store/config.json`. The lead stops
  dispatching when `ledger.py status` reports `over_budget`.
- Do not read files you were not told to read. Do not put images or meshes
  into your own context or into a brief. Paths only.
- Prefer the script to the turn: polling, arithmetic, validation and API
  calls are scripts.
- Paid generation (fal) runs only through `design_product.py`, which counts
  every accepted request against `design.usd_cap_per_item`. Never call a
  generation script directly and never pass `--force` to one.

## Secrets and privacy

- Keys and hosts come from env. Never print, log, or write `DAPI_API_KEY`
  or any `*_KEY` variable, including into `log.md` or a result line.
- Never put a real customer's name, address or order contents into a
  summary, a report, or a brief. Refer to orders by id.
- Skills are public. Nothing store-specific goes into a skill; it goes into
  `store/` and is read from there.

## When in doubt

Stop and return `needs_human` with a one-line question. A blocked stage
costs nothing. A wrong publish, payment or status change costs a person's
afternoon.
