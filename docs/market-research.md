# Market research and pricing

How dapilot decides whether a model can sell and at what price. This is
the record of what is built as of 2026-09-11 and what was learned running
it. Read `CLAUDE.md` first; the stage table
is in `docs/agents.md` and the skill rules in `docs/skills.md`.

## 1. The question

Given one model file, dapi's quote for printing it, and the market on Etsy:
can it be listed at a price that clears cost, fees and tax, and if so at
which price. Everything below exists to answer that with numbers a script
computed and a judgement a model wrote down.

## 2. The stage order

Since 2026-09-09 intake runs in this fixed order:

| Stage | Skill | Produces | What it costs |
|---|---|---|---|
| analyze | `analyze-model` | `analysis.json` | seconds |
| quote | `quote-model` | `quote.json` | one dapi slice, 15 to 40 s |
| preview | `preview-model` | `renders/draft_00..07.png` | about 30 to 70 s of CPU |
| research | `research-comparables` | `comparables.json`, `viability.json` | one to three Etsy searches, about 0.45 USD of worker |
| price | `propose-price` | `price.json` or `price.rejected.json` | about 0.20 USD of worker |
| render | `render-model` | `renders/hero.png`, later the video | minutes of CPU |
| copy | `write-listing` | `listing.json` | not built |
| publish | `publish-listing` | `publish.json` | not built, Propose gate |

Research and pricing come before the expensive renders so an item that
cannot sell costs one slice and eight small drafts. `price.json` exists only
for a sellable item; render, copy and publish require it.

## 3. The research stage today

Five steps, alternating judgement and script. Rewritten 2026-09-11 for the
deep read; section 7 has the mechanics.

1. **Name the product** (model). From the filename, the analysis and two
   drafts: the noun sellers put in titles, a two or three word buyer query,
   and the taxonomy id of the aisle. The id is required whenever a node
   contains the product.
2. **Search** (script, `etsy_search.py search --noun`). One relevance page
   per price window of the aisle, filtered as it lands: physical, not a file
   or supply, noun in the title, stated size within half to double ours.
   Inventory read for survivors with variants. Compact records to
   `research/etsy.json`, full ones to `research/etsy.full.json`, the
   windows under `pages`. Then `market_stats.py --summary` writes
   `research/summary.json`: material classes, a price histogram, the
   heaviest titles, a deterministic sample.
3. **Write rules** (model). From the summary only: required words, excluded
   words, allowed and excluded materials, one sentence of notes. No
   thresholds, no numbers.
4. **Score, select, band** (script). `etsy_search.py score` then
   `market_stats.py --rules`: relevance and market weight per listing,
   selection at relevance 0.6, `comparables.json` ordered by weight with a
   shop block per record, `viability.json` with the weighted band, the
   unweighted band, `spread_ratio`, `thin` and the verdict. Then one review
   by count of the selected top by weight against the drafts, and at most
   one tightening of the words.
5. **Log and result line.**

Scripts: `etsy_search.py`, `etsy_auth.py`, `market_stats.py`, `pricing.py`.
Store-private inputs: `$DAPILOT_STORE_DIR/etsy_token.json`,
`reference/pricing-policy.json`, optional `reference/platform-fees.json`.

## 4. The price chain

Every number in this chain is in `pricing.py`, shared by the two scripts.

```
manufacturing  = setup + grams x filament rate + hours x time rate + post-processing + extras
quote          = manufacturing x (1 + api_commission / 100)        dapi's price for one unit
plate unit     = the unit price at max_per_plate from dapi's batch curve  what a plate of them costs each
cost           = plate unit (policy cost_basis full_plate), VAT added if the seller cannot reclaim it
net(P)         = P / (1 + VAT rate) when VAT sits inside the price, else P
fees(P)        = listing fee + payment fixed + P x (transaction pct + payment pct + extra pct)
margin(P)      = net(P) - fees(P) - cost
break-even     = the P with margin 0, solved in closed form
```

Decided 2026-09-09 and 2026-09-11 for dapi's own shop:

- The cost basis is dapi's batch price as charged: the unit price at a
  full plate, from the batch curve on the quote. The shop buys in runs of
  about 100 and sells from stock, so that is the real unit cost. For an
  object that fits one per plate it equals the one-off quote. dapi's
  commission is never backed out: whoever produces on the platform pays
  it, us included. The one-off quote is reported beside the plate price in
  every viability file as the road not taken (`cost_basis: one_off`).
- Two candidates go to the model: the quote plus 50 percent markup, and
  the market median. Both rounded to a .99 ending.
- Hard rule: the sale price is never below break-even, for any author of
  `price.json`. `price_candidates.py --check` refuses it with exit 3. A
  person may override to another price with an `override` block, never
  below the floor. The model never writes an override block.
- Soft rule: candidates under `min_margin_pct` (10 percent) are not
  proposed by the model.

Platform fees, verified 2026-09-09:

| | Etsy | eBay |
|---|---|---|
| listing | 0.20 USD per sale | first 250 a month free, then 0.35 |
| commission | 6.5 percent of item plus shipping | 13.6 percent of item plus shipping plus tax |
| processing | US 3 percent + 0.25; EU 4 percent + 0.30 EUR; UK 4 percent + 0.20 GBP | included |
| per order | none | 0.30 under 10 USD, 0.40 above |
| ads | Offsite Ads 15 percent on attributed sales, mandatory at 12 percent above 10k a year | optional |

Shipping on Etsy is charged to the buyer on top of the item price through
the seller's shipping profile, and Etsy takes its percentage on it too.
Listing prices in the band are item prices. Other shops' shipping profiles
cannot be read through the API any more, so shipping is compared at cost on
both sides until dapi's shipping rates feed the fee file.

## 5. What the first runs showed

Hand-picked comparables, one-off cost basis, 2026-09-09:

| Item | comparables | median | quote | break-even | verdict |
|---|---|---|---|---|---|
| octagon passthrough fidget, K2 project | 6 | 6.49 | 5.71 | 8.65 | not viable |
| mini duck charm, SPARKX project | 9 | 1.93 | 2.59 | 4.32 | not viable |

Both objects fit one per plate, so the batch basis adopted later changes
nothing for them; section 7b has the same items rerun on the deep read.

Lessons, all now in code or config:

- VAT dominates the floor. With 21 percent inside every sale, half the gap
  between quote and break-even is VAT. The policy assumes every buyer is in
  the EU, which is the worst case; blending the rate to the real EU share
  is the single most important number to fix.
- Small quotes cannot carry fixed fees. 0.53 USD of fixed fees is 9
  percent of a 5.71 quote and 20 percent of a 2.59 one. Objects that clear
  the floor are the ones the market pays well per gram.
- The 50 percent markup candidate is rejected for margin on every small
  quote, so the median is usually the only live candidate.
- Foreign 3mf projects (Creality Print, Orca, Prusa) fail on dapi's slicer
  until `strip_3mf.py` removes their settings. The server does this on
  upload.
- Quote metrics were per plate, not per unit, on batched quotes until the
  API fix of 2026-09-09. Quotes sliced before it must be re-sliced.

## 6. What supervision required

Three faults from the first supervised runs, each fixed:

- The ledger flipped a running stage to done when its file appeared, so
  every worker result was rejected and no cost was kept. A running stage
  now belongs to its worker until recorded or stale.
- A stalled worker held its item for two hours because only `claude -p`
  was killed on timeout. Workers now run in their own process group and
  are killed at `max_worker_minutes`; a lock older than that is stale.
- The research worker ran out of its 0.50 USD budget reading 65 KB of
  listing JSON, then its dead lock parked the item. The search output is
  compact, budgets and turn caps are per stage
  (`budget.usd_per_stage`, `turns_per_stage`), and the ledger drops the
  lock when it records the worker's end.

Every run keeps `runs/<id>/state.json`, `report.md`, `director.log` and
each worker's raw output under `workers/`. `scripts/dev/up.sh` checks the
prerequisites and starts the job API and the console; unfinished work is
listed at `/runs/unfinished` and resumed with `POST /runs/resume`,
`run_intake.py --resume`, the console's Resume button, or
`resume_on_start` in the store config.

## 7. The deep market read (built 2026-09-11)

Built by two workflows of six agents each and hand-finished. What stands:

**Paging by price ladder, not offset.** Etsy refuses any search offset above
5 for our key, with or without the OAuth token, so one query returns its
top 100 by relevance and nothing more. The read therefore pages by price
window instead: `--ladder-edges` default 0, 2, 4, 8, 16, 32, 64, 128, 256
USD and an open top, one relevance page per window, listings deduplicated
across windows. Each window is one search call and one batch call; the
inventory pass runs for survivors only. A window is skipped once two
windows in a row after a wet one fall under a 20 percent hit rate; empty
windows count as neither. `pages` in the output records every window.

**Two scores per listing, in `etsy_search.py` and imported by
`market_stats.py`** so the formulas cannot drift: relevance from the noun,
the model's word lists, the material class and the size ratio; market
weight from sales and favourites. Word matching is whole-word, plural
tolerant, hyphen tolerant and accepts digits glued on the left.

**Rules, not verdicts.** The model reads `research/summary.json` (material
classes, a price histogram, the heaviest titles, a deterministic sample)
and writes `research/rules.json`: required words, excluded words, allowed
and excluded materials, notes. It never writes a threshold; selection is at
relevance 0.6 by script default, and the only tightening levers are words.
`market_stats.py --rules` scores every kept record, writes
`comparables.json` ordered by market weight with a shop block per record,
and `viability.json` with a weighted band by cumulative mass, the
unweighted band beside it, `spread_ratio` and a `thin` flag.

**Review by count.** The model checks the selected comparables in
`top_by_market_weight` against the drafts once and tightens the words once
at most: more than 4 of 20 not our product, or more than 1 when fewer than
10 are listed. A set or bundle counts as not ours; if words cannot remove it
without removing genuine comparables, it stays and the log says so.

**p75 as a third candidate.** `price_candidates.py` adds the band's p75,
skipped when it rounds to another candidate's price, and reports
`p75_sellers`: the weight share of each named shop among listings at or
above p75. The pricing skill proposes p75 only when the band is weighted,
the drafts show ours at least as good as the median listing, and the p75 is
held by at least two named shops with the largest under 60 percent of the
weight. A thin band means the markup candidate when it exists, else the
sole candidate with the thinness stated.

**What the fixtures showed.** Batch cost basis; the fidget and the duck
fit one per plate, so their unit cost is the one-off quote.

| | fidget, aisle 1628 | duck, aisle 1799 | duck, no aisle | cat clicker, aisle 1628 |
|---|---|---|---|---|
| per plate | 1 | 1 | 1 | 8 |
| windows fetched / skipped | 6 / 3 | 9 / 0 | 9 / 0 | 9 / 0 |
| kept after filters | 9 | 15 | 404 | 205 |
| selected by rules | 3 | 12 | 288 | 170 |
| weighted median / p75 | 10.48 / 10.48 | 2.57 / 2.58 | 43.35 / 151 | 8.00 / 11.99 |
| unit cost / floor | 5.71 / 8.99 | 2.59 / 4.99 | 2.59 / 4.99 | 1.83 / 3.99 |
| thin | yes | yes | yes | no |
| outcome | median 9.99, 12 percent | rejected | a different product's band | median 7.99 at 52 percent, p75 11.99 at 63 percent |

The aisle is the market. Inside it the ladder reads everything and small
aisles give thin bands honestly. Without it the noun matches jewellery and
decor with unstated materials, and the band is meaningless. The taxonomy id
is therefore required whenever a node contains the product. The "hundreds
of listings" ambition holds for big aisles only; for small ones the whole
aisle is the answer.

**Known limits.** Etsy's windows are inclusive at both edges and bin by
Etsy's own price notion, so a listing can surface in a window its recorded
price is outside of; counts per window can double-count edge prices while
listings are deduplicated. Market weight rewards incumbents. The stop rule
can end a read early when cheap junk sits low in the ladder under a product
priced higher.

## 7b. Cost basis: the batch price (decided 2026-09-11)

dapi's batch curve gives a unit price per quantity up to `max_per_plate`.
Until 2026-09-11 every floor used the one-off quote. `pricing-policy.json`
now has `cost_basis: full_plate` (the unit price at a full plate, what a
stock run costs each) with `one_off` kept as the print-to-order
alternative. Both scripts honour the policy, both report the other basis
beside it, and `--cost-basis` overrides it for one run. The verdict and the
floor follow the policy basis.

The clickerfactory cat, eight per plate, in aisle 1628 with the query "cat
clicker fidget": 205 listings kept across nine windows, 170 selected by
rules, 42 named shops at or above the p75.

| | one-off | full plate |
|---|---|---|
| dapi unit price | 4.97 | 1.83 |
| floor | 7.99 | 3.99 |
| weighted median 8.00 | rejected, 4 percent margin | 7.99 at 52 percent |
| weighted p75 11.99 | 32 percent margin, sole candidate | 63 percent |

Same market, same object: print to order leaves one price and a thin
margin; a plate of eight makes the median comfortable and the p75 rich.

Why the batch price is the basis: the shop does not print to order. It
buys a run of about 100 and sells from stock, so the plate price is what a
unit actually costs, and the market analysis has to be read against it or
every small object looks unsellable. The policy is `cost_basis: full_plate`
with `stock_units: 100`.
`viability.json` then carries a `stock` block: plates for the run, batch
cost at the plate price, and at p25, median and p75 the margin per unit,
the units that pay the batch back, and the profit if every unit sells. For
the cat: 13 plates, 183 USD, paid back after 54 sales at the median or 30
at the p75, 629 USD profit if all 100 sell at 11.99. For a one-per-plate
object nothing changes except that the block shows the stock risk: the
fidget's 100 units cost 571 USD and pay back after 433 sales at its
median, which is the honest way to say it is not a stock item.

The thin flag was also corrected on this item: a 170-listing aisle spans
20x from junk to premium and its median is solid, so the spread test now
applies only under 20 listings.

**What this means for choosing objects.** The fixed platform fees and the
VAT do not shrink with the object, so a small object is viable only if the
plate makes it cheap: the cat at eight per plate goes from a rejected
median to a 52 percent one, while the fidget and the duck at one per plate
stay where they were. The batch curve, not the market, is the selection
rule for small items: an object that packs is a candidate, an object that
does not is not, and `viability.json` says which with `max_per_plate`,
`plate_discount_pct` and the `stock` block.

## 8. Open decisions

- The VAT share of sales in the pricing policy.
- Whether dapi's own shop pays itself the commission (today: yes, by
  keeping the quote as cost).
- `stock_units` is a single number for the whole shop; per-item batch
  sizes would need a field on the item.
- Shipping: dapi's rates by zone into the fee file, which needs an
  ORDER-scoped key and account approval.
- The six old fixture work dirs that a blanket resume would spend money on.
