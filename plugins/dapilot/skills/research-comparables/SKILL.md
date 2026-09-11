---
name: research-comparables
description: Finds what one model competes with on Etsy and writes the weighted market band and our margin at it, by naming the product, reading a market summary, writing the word rules that score every listing, and reviewing the heaviest sellers once. Use as the research stage once meta.json, analysis.json, quote.json and renders/draft_00.png exist and comparables.json does not. Not for choosing a price (propose-price) or for copy (write-listing).
allowed-tools: Read, Bash, Write
---

# research-comparables

## When
A brief with `stage: research`. The work dir has `meta.json`, `analysis.json`,
`quote.json` and `renders/draft_00.png`, and no `comparables.json`.

## Inputs
- `meta.json`: the original filename, the strongest hint at what the object is
- `analysis.json`: `bbox_mm`, `bodies`
- `renders/draft_00.png` and one more draft: what it looks like
- `quote.json`: `unit_price`, `batch`, `metrics.model_g`
- `research/summary.json`, written in step 3 and refreshed in step 4: the
  only research file the judgements read. The compact `research/etsy.json`
  and the full file exist for the scripts; the log's counts come from stdout.

Environment: `ETSY_KEYSTRING`, `ETSY_SHARED_SECRET`, `DAPILOT_STORE_DIR`; a
script fails with a one-line JSON error when one is missing. Pass it on.

## Procedure
1. If `comparables.json` exists, print a `cached:true` result and stop.
2. Judge: name the product from `meta.json`, `analysis.json` and two drafts:
   the noun (one word, singular, the word sellers put in the title), a buyer
   query of two or three words containing it, and the taxonomy id of the
   aisle the product is sold in, from
   `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/etsy_search.py taxonomy find "<word>"`.
   The id is not optional when one fits: the ladder reads the whole aisle,
   and without an aisle it reads every product that carries the noun.
   How to choose all three: `reference/search-guide.md`.
3. Run, with the defaults for the ladder and its stop rule:
   `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/etsy_search.py search "<query>" --noun <noun> [--taxonomy-id N] --analysis $WORKDIR/analysis.json --currency USD --out $WORKDIR/research/etsy.json`
   The search fetches by price window, each in Etsy's own relevance order: one
   query is one call per window, and the windows are the `pages` it reports.
   Exit 2 means nothing survived: run one broader query as the guide says,
   once. Exit 1: `ok:false`, reason from stderr. Then run
   `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/market_stats.py --workdir $WORKDIR --summary research/summary.json`
   which writes the market summary without scoring anything.
4. Judge: write `research/rules.json` from `research/summary.json` and the
   drafts, schema under Outputs, following the guide: `required_any` names
   the mechanism or kind, `excluded` the neighbouring products,
   `materials_allowed` and `materials_excluded` come from `materials_top`,
   `notes` is one sentence. Words only: the relevance threshold is the
   script's default and this skill never writes one. Then run, in order:
   `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/etsy_search.py score --full $WORKDIR/research/etsy.full.json --rules $WORKDIR/research/rules.json --out $WORKDIR/research/etsy.json`
   `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/market_stats.py --workdir $WORKDIR --rules research/rules.json --summary research/summary.json`
   The second writes `comparables.json` and `viability.json` and rewrites
   the summary with `relevance` and `excluded_by` on every title. Exit 2
   means no comparable had a price: see Failure. Then judge, once: in
   `top_by_market_weight` of `research/summary.json`: once rules exist it
   lists the selected comparables only (each row carries `selected: true`;
   the heaviest rejects sit apart under `top_rejected_by_market_weight`),
   heaviest first. Against the drafts, count how many of the first 20 are
   not our product; a set or bundle of the product counts as not ours.
   Tighten when more than 4 of 20 are not ours, or when fewer than 10 are
   listed and more than 1 is not: add words to `excluded`, `required_any`
   or `materials_excluded` as the guide says, nothing else, and rerun the
   two scripts once. Never a third pass. One exception: when the only wrong
   entries are sets or bundles and no word removes them without removing
   genuine comparables, keep the rules, keep them in, and say so in the log.
5. Append the log paragraph: noun, query, taxonomy id, windows and stop reason,
   fetched, kept, scored, selected, whether the rules were tightened and why,
   weighted median and p75, basis, `market.thin` as printed, unit cost,
   break-even, margin at the median, verdict, quota left. Remove `.lock`.
   Print the result line with the verdict in `summary`.

## Outputs
- `research/etsy.json` and `research/etsy.full.json`: the search, with `noun`,
  `pages` (one per price window), `stop_reason`, and after scoring `relevance`,
  `market_weight` and `excluded_by` per record.
- `research/summary.json`: counts by material class, `materials_top`, price
  quantiles and histogram, `top_by_market_weight`, `sample_every_kth`.
- `research/rules.json`, the one file this skill writes by hand:
  ```json
  {"noun": "duck", "required_any": ["charm", "mini", "miniature"],
   "excluded": ["set of", "bundle", "keychain", "articulated"],
   "materials_allowed": ["pla", "resin", "3d printed"], "materials_excluded": ["clay", "metal", "wood"],
   "taxonomy_id": 1799, "notes": "one sentence"}
  ```
- `comparables.json`, by `market_stats.py`: `mode: "rules"`, the rules used,
  `n_scored`, `n_selected`, `comparables` ordered by `market_weight`
  descending with `relevance`, `page` and a `shop` block on each,
  `rejected_count`, `rejected_sample`.
- `viability.json`, schema as printed by `market_stats.py`: `market` with
  `basis`, `weighted` and `unweighted` bands, `spread_ratio` and `thin`,
  per-gram, unit cost, fees, margins, break-even, and a `verdict` of
  `viable`, `thin` or `not_viable`. Paths in it are relative to the work dir.

## Gate
Auto. Etsy reads only; no dapi call. The one spend is Etsy quota: one search call
per price window, one batch call per 100 survivors, one inventory call per
survivor with variants. At most two searches per item.

## Failure
- Missing Etsy credentials or `DAPILOT_STORE_DIR`: a script exits 1 with a one-line
  JSON error; `ok:false`, that line as `reason`.
- HTTP 429 after the script's own retry: `ok:false`, "etsy rate limited"; the ledger retries once.
- Nothing survives two searches: still write `rules.json` with the noun and
  the guide's default lists, run the two scripts of step 4 so that an empty
  `comparables.json` and a `viability.json` with `ok:false` exist, and
  return `ok:true` with `needs_human` "no priced comparables for <query>;
  skip the model or supply a query".
- `market_stats.py` exits 2 with listings kept: the rules selected nothing. That
  is the one rerun of step 4, removing words instead of adding them, guided by
  `rejected_sample`. If it exits 2 again, finish as the case above.
- `quote.json` without `metrics.model_g`: per-gram fields are null, not a failure.

## Do not
- Do not judge listings one by one. Rules score them; you review twenty titles, once.
- Do not write a threshold, a ratio or any number into `rules.json` other
  than the taxonomy id. Words are the only lever.
- Do not edit `comparables.json` by hand or put a number anywhere a script
  did not print. A thin band is reported from `market.thin`, never worked
  out in prose and never fixed by changing the rules.
- Do not read `research/etsy.json` or the full file; the summary is the view.
- Do not propose a price, search more than twice, or run the rules a third time.

`$PY` is `${DAPILOT_PYTHON:-python3}`.
