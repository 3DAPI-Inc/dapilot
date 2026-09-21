---
name: propose-price
description: Chooses the sale price for one model from the candidates the pricing script allows, with a written rationale, and has the script verify the choice. Use as the price stage once quote.json, comparables.json, viability.json and the preview drafts exist. Not for computing a price (price_candidates.py), not for market research (research-comparables), not for copy.
allowed-tools: Read, Bash, Write
---

# propose-price

## When
A brief with `stage: price`. The work dir has `quote.json`,
`comparables.json`, `viability.json` and `renders/draft_00.png`, and no
`price.json`.

## Inputs
- `price.candidates.json`, written by step 2: the only prices this skill may
  choose from, `skipped` with its reasons, and `p75_sellers` (who holds the
  weight at or above the p75)
- `comparables.json`: the listings, their prices, sales, favourites, shop
  and market weight (estimated units sold per day, from the shop's recent
  reviews; `sales_est` shows the reviews and window behind it), heaviest
  first
- `viability.json`: `market.basis`, `market.thin`, the band, the verdict
- `renders/draft_00.png` to `draft_07.png`: what ours looks like
- the comparables' `images` URLs, only if a Read of them is needed to settle
  finish or complexity

Environment: `DAPILOT_STORE_DIR`, for the pricing policy. The script fails
with a message when it is missing; pass it on.

## Procedure
1. If `price.json` exists, print a `cached:true` result and stop.
2. Run `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/price_candidates.py --workdir $WORKDIR`.
   - Exit 0: `price.candidates.json` holds one to three candidates
     (`markup`, `median`, `p75`) above the floor and the minimum margin;
     `skipped` says why `p75` is absent when it is. Continue.
   - Exit 2: `price.rejected.json` is written; no candidate is allowed.
     Return `ok:true`, outputs `["price.rejected.json"]`, `needs_human` set
     to one line: "<product>: no price clears the floor <floor> at the
     minimum margin; skip, or override with a price above the floor". Stop.
   - Exit 1: `ok:false`, reason from stderr.
3. Judge: pick one candidate. Read `price.candidates.json`, `comparables.json`,
   `viability.json` and the drafts, then answer one question: which of the
   allowed prices will the buyer who sees ours next to those listings
   accept? `reference/positioning.md` gives the tests. The three p75 tests:
   (a) `market.basis` is `weighted`;
   (b) the drafts show ours at least as good as the median listing: same
       object, no fewer parts, no worse finish;
   (c) `p75_sellers` shows the p75 is not one shop's price:
       `distinct_named_shops` is 2 or more, `top_share` is under 0.6 and
       `unnamed_share` is under 0.2. A null `p75_sellers` means (c) is
       unknown and fails.
   Which case applies is read from the candidates, in this order:
   - `market.thin` is true: `markup` when it is a candidate. Otherwise the
     `median` when it is one, otherwise the sole candidate. The rationale
     says the band is thin, as printed. A thin band never picks `p75` over
     an allowed `median`.
   - `median` and `p75` are both candidates: `p75` when (a), (b) and (c)
     all hold, else `median`. `markup` alongside them is the cost-anchored
     fallback for an unreliable band, not a third market position.
   - `p75` is the sole candidate: the question is whether it is defensible
     in the band, as the guide's one-candidate section says. Propose it
     with position `premium`, and the rationale states which of (a), (b),
     (c) held and which failed. A person decides at publish; the rationale
     is written for that person.
   - `median` is the sole candidate: propose it. `markup` alone: propose it
     and say the market gave nothing else.
   Write `price.json`:
   ```json
   {"price": 19.99, "currency": "USD", "candidate": "p75",
    "position": "premium",
    "rationale": "three sentences: what the comparables sell at and how well, what ours has or lacks, which of (a) (b) (c) held or failed and why this candidate",
    "runner_up": {"candidate": "median", "price": 13.99, "why_not": "one sentence"},
    "floor": 8.99, "verdict": "viable"}
   ```
   `price` is copied from the candidate, never typed. `runner_up` is null
   when there was one candidate.
4. Run `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/price_candidates.py --workdir $WORKDIR --check`.
   Exit 0: continue. Any other exit: delete `price.json`, return `ok:false`
   with the check's `errors` as the reason. Do not adjust the price to pass.
5. Append the log paragraph: floor, band basis, whether the band is thin,
   candidates with margins, the choice, the position, which p75 tests held
   or failed, one line of why. Remove `.lock`. Print the result line with
   the price and margin in `summary`.

## Outputs
- `price.candidates.json`, by the script, unchanged.
- `price.json`, schema above, only when a candidate was chosen and the check
  passed. With a person's `price.approved.json` it is what lets design, copy
  and publish run.
- `price.rejected.json`, by the script, when nothing is allowed.

## Gate
Auto to write the proposal. A person approves or overrides it afterwards,
which writes `price.approved.json`; the design stage requires that file, so
the proposal alone starts nothing. Nothing here touches dapi or Etsy.

## Failure
- Missing `DAPILOT_STORE_DIR` or the policy file: script exits 1; `ok:false`.
- `viability.json` with `ok:false` (no comparables): the script exits 1;
  return `ok:false`, reason "no market band; research found no
  comparables". The ledger retries once and then reports it.
- `viability.json` without `market.thin`: a band written before the flag
  existed. Read it as not thin and say so in the log.
- The check fails on the model's own output: `ok:false` as in step 4. The
  retry brief carries the errors.

## Do not
- Do not write a price that is not in `candidates`. Not rounded, not
  averaged, not "a bit above". The check will refuse it.
- Do not write an `override` block. Only a person does, by hand.
- Do not compute margins, break-even, shares or a spread. Read them; thin
  comes from `market.thin` and nowhere else.
- Do not pick the higher candidate because it earns more. When `median`
  and `p75` are both allowed, `p75` is proposed on (a), (b) and (c) or not
  at all. When `p75` is the only candidate it is proposed with the failed
  tests named, never with them hidden.
- Do not look at more than two comparable images. The band is the
  evidence; photos settle finish, not price.

`$PY` is `${DAPILOT_PYTHON:-python3}`.
