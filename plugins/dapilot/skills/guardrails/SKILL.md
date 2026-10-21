---
name: guardrails
description: The limits every dapilot agent works within, gates on actions, budgets, no reruns, no loops, secrets. Loaded into every agent. If a step would cross one of these, stop and report instead.
user-invocable: false
---

# guardrails

## Gates

There is one approval in dapilot: a person approves the sale price. It is
given at the gate (`/dapilot:review`, or the console), it is recorded as
`price.approved.json`, and it authorises everything after it: the photos,
the paid lifestyle assets within `design.usd_cap_per_item`, the listing
text, and publishing in the mode set on the Settings tab (`publish.mode`:
live or sandbox). Nothing before the price needs an approval and nothing
after it asks for one again.

So: never ask a person whether to run a stage, spend within a cap, or go on
after the price. Do not describe a step as something to approve. The only
reason to stop with `needs_human` is a decision no stage owns, and that
should be rare: a missing credential (point at `/dapilot:config`), an input
that is not a model file, a deletion.

Operations on 3DAPI that create an order, pay, or change an order's status
are not part of intake; `GATES` in `scripts/dapi.py` keeps them at
`propose` for the day a skill needs them. Never pass `--confirm` to a
script. Never change a gate, a stage table, a budget, or an agent file
during a run.

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
- The `USD budget: $x/$y` line Claude Code adds to your reminders is your
  own token spend for this session, set by the director. It is not the
  asset cap and fal spend never counts against it. When `check` passes,
  the plan is within the cap: run `produce`; never refuse a plan because
  its cost is near or above the session figure.

## Secrets and privacy

- Keys and hosts come from env. Never print, log, or write `DAPI_API_KEY`
  or any `*_KEY` variable, including into `log.md` or a result line.
- Never put a real customer's name, address or order contents into a
  summary, a report, or a brief. Refer to orders by id.
- Skills are public. Nothing store-specific goes into a skill; it goes into
  `store/` and is read from there.

## When in doubt

If a stage's inputs are there and its script runs, do it; the price gate
upstream already said yes. Return `needs_human` only for a decision no
stage owns, with a one-line question, and never for "may I spend", "may I
make the design" or "is this price right": the script caps the spend, the
stage table orders the work, and the person set the price.
