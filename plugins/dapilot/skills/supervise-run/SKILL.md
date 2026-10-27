---
name: supervise-run
description: Decides, for one stopped stage of a run, whether to retry it, redo an earlier stage whose output is the real problem, or leave it for a person, and says why in one sentence. Used by the hosted runner's supervisor (a model since 2026-10-26; rules before). Not for answering a stage's question to a person (a blocked marker is never sent here), not for touching files (the supervisor has no tools), not for pricing or design.
---

# Supervise one stop

## When this runs

A stage of an item ended `failed`, or ended `done` with a review marker, and
the runner's supervisor loop sends the stop to you once per outcome. A stage
that asked a person a question (`blocked`) is never sent: a retry is not an
answer.

## The question

Not "what went wrong". The runner knows what went wrong; it is in the reason.
The question is **what would make the next attempt end differently**, and
whether that thing is yours to do. Three answers exist.

| action | when | what the runner does |
|---|---|---|
| `retry` | the next attempt of the same stage can plausibly end differently: an upstream hiccup (5xx, 529, rate limit, a queue deadline), a timeout on a first attempt, a tool that was briefly unavailable, or the seller's configuration changed since the stop and the stop was about configuration | clears the marker and queues the stage again, after `delay_s` seconds when you give one |
| `redo` | the stopped stage is fine and its **input** is the problem: an earlier stage's output is empty, unparseable, stale or wrong, which the reason, the account or the report says plainly ("analysis.json is empty", "quote.json has no metrics", "the drafts show the plate, not the model") | sets aside the named earlier stage's outputs and everything after them up to the stop, then runs from there. Name the earliest stage that must run again in `stage` |
| `leave` | nothing you can do would change it: a credential or variable is missing or refused (a person sets it), the file itself cannot be processed (empty geometry, exceeds the plate, the slicer refuses it), the same failure already happened twice, the stage asked for a person, or a paid stage would have to be redone (fal was spent) | writes your sentence under the failure on the dashboard, with `wait_for` naming what has to change |

## How to decide

1. **Read the reason and the code first.** A code from a script
   (`config.env_missing`, `config.key_rejected`, `upstream.transient`,
   `upstream.quota`, `content.file`, `content.market`, `worker.timeout`,
   `worker.malformed`, `runtime.tool_missing`) is a fact; trust it over what
   the text looks like. Without a code, read the sentence.
2. **Then read the account and the report.** The worker's own paragraph says
   what it tried and saw; the stage's report says what it made. A reason that
   names an input file, or an account that says "the drafts show nothing" or
   "analysis says 0 bodies", points at an earlier stage: that is a `redo`.
3. **Then the history.** `previous` is what the stage ended with before its
   last retry. The same failure twice is not a transient; leave it. A
   different failure on the retry is new information; judge it fresh.
   `retries_left` is the runner's cap; at 0 your answer is `leave` whatever
   you think, and the runner will enforce it anyway.
4. **Then the money.** A `redo` that would set aside a stage with fal spend
   (`design` with assets, `spend.assets` above 0) is a person's decision, not
   yours: leave, and say which stage would have to be paid for again.
5. **Transients get a delay, not a hammer.** An overloaded API or a rate
   limit: `retry` with `delay_s` of 120 to 600. A queue deadline that kept
   partial work (fal, the slicer still processing, a capture that resumed):
   `retry` with 60. A first timeout of a worker: `retry` now, once.
6. **Configuration is a person's.** Missing or refused credentials, an
   unknown department, a tool missing from the image: `leave` with
   `wait_for` saying exactly what must be set or installed. The one
   exception: `changed` is true, meaning the seller's configuration moved
   since the stop, and the stop was about configuration: then `retry`.
7. **Content is a person's.** A file the slicer refuses, a model over the
   plate, no geometry, a market with nothing in it: `leave` with a sentence
   the seller can act on. Do not retry a fact about the file.
8. **A review marker is a done stage.** Redo it (`retry` on the same stage)
   only when the problem is a plain defect a fresh attempt would fix (a
   price under its own floor, no photos kept, no product name) and only once;
   otherwise `leave` with the note.
9. **Unknown is allowed.** A reason nothing above fits: say what you think it
   is in `why`, and choose `leave` unless the text itself says it was
   momentary. Never invent a retry to look useful.

## The answer

One line of JSON, nothing else:

```json
{"action": "retry", "delay_s": 0, "why": "one sentence a seller can read", "wait_for": null, "stage": null, "confidence": "high"}
```

- `action`: `retry`, `redo` or `leave`.
- `stage`: for `redo`, the earliest stage to run again (one of analyze,
  quote, preview, research, price, design, copy, publish); null otherwise.
- `delay_s`: for `retry`, seconds to wait first; 0 for now.
- `why`: one sentence, plain words, naming the cause and the choice. It is
  shown on the dashboard under the failure and kept on the ledger's record,
  so it carries no money figure: the spend you are given is for the money
  rule, and the seller pays in credits and never sees what a stage cost.
- `wait_for`: for `leave`, what has to change for a retry to be worth it
  ("ETSY_SHARED_SECRET set on the platform", "a file that fits the plate",
  "a person's decision to redo a paid design"); null otherwise. The same
  rule: no figure.
- `confidence`: `high`, `medium` or `low`; low means you would like a person
  to glance at it even if the action is a retry.

The runner validates the line, enforces the caps and the money rule, and
falls back to its fixed rules if you answer anything else. Fixtures of real
stops with the expected actions are in `eval/stops.jsonl`; `scripts/dev/
test_supervisor.sh` runs them.
