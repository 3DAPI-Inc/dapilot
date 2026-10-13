# Market research and pricing

How dapilot decides whether a model can sell and at what price. This is
the record of what is built as of 2026-09-17 and what was learned running
it. Read `CLAUDE.md` first; the stage table
is in `docs/agents.md` and the skill rules in `docs/skills.md`.

## 1. The question

Given one model file, 3DAPI's quote for printing it, and the market on Etsy:
can it be listed at a price that clears cost, fees and tax, and if so at
which price. Everything below exists to answer that with numbers a script
computed and a judgement a model wrote down.

## 2. The stage order

Since 2026-09-09 intake runs in this fixed order:

| Stage | Skill | Produces | What it costs |
|---|---|---|---|
| analyze | `analyze-model` | `analysis.json` | seconds |
| quote | `quote-model` | `quote.json` | one 3DAPI slice, 15 to 40 s |
| preview | `preview-model` | `renders/draft_00..07.png` | about 30 to 70 s of CPU |
| research | `research-comparables` | `comparables.json`, `viability.json` | one to three Etsy searches, about 0.45 USD of worker |
| price | `propose-price` | `price.json` or `price.rejected.json` | about 0.20 USD of worker |
| design | `design-product` | `design.json`: stills, lifestyle media, video | about 0.45 USD of worker, up to 2.00 USD of paid assets |
| copy | `write-listing` | `listing.json` | disabled until rebuilt for Etsy |
| publish | `publish-listing` | `publish.json` | disabled until rebuilt for Etsy, Propose gate |

Research and pricing come before the expensive media so an item that
cannot sell costs one slice and eight small drafts. `price.json` exists only
for a sellable item; design, copy and publish require it. (`design`
replaced `render` on 2026-09-13.)

## 3. The research stage today

Five steps, alternating judgement and script. Rewritten 2026-09-11 for the
deep read; section 7 has the mechanics.

1. **Name the product** (model). Decided 2026-09-21: the pictures decide
   and the filename is a witness. The worker reads the 3MF's own previews
   when the project carried them (`thumbnails/plate_1.png`, `top_1.png`,
   copied out by `source_thumbnails.py` at `ledger init`; in colour, every
   part as laid on the bed), two drafts, the analysis, and last the
   filename, and writes `identity` into `rules.json`: the object in a few
   words, `confidence` of high, medium or low, the `witnesses` that
   supported the reading, one sentence of reason. `market_stats.py`
   checks the shape and copies the block into `comparables.json` and
   `viability.json`; the price skill repeats a low reading before the
   number and the console shows it in red above the band. The reason: on
   `test2.stl` the worker read "a curved upright with a top lip and a foot"
   as a phone stand, said in its log it was guessing, and nothing
   downstream heard it. Then the noun sellers put in titles, a two or
   three word buyer query, and the taxonomy id of the aisle. The id is
   required whenever a node contains the product.
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

Scripts: `source_thumbnails.py`, `etsy_search.py`, `etsy_auth.py`, `market_stats.py`, `pricing.py`.
Store-private inputs: `$DAPILOT_STORE_DIR/etsy_token.json`,
`reference/pricing-policy.json`, optional `reference/platform-fees.json`.

## 4. The price chain

How a sale price comes to exist, end to end. The model never computes a
number: scripts compute at most three candidate prices, the model picks one
and writes why, and a script checks the pick. Each step reads and writes
files in `work/<sha256>/`.

| Step | Who | Reads | Writes |
|---|---|---|---|
| 1. cost | `quote.py` (quote stage) | the model file | `quote.json`: `unit_price`, `batch`, `metrics` |
| 2. market band | `market_stats.py` (research stage) | `comparables.json`, `quote.json`, policy, fees | `viability.json`: band, `thin`, verdict, `stock` |
| 3. candidates | `price_candidates.py` (price stage) | `quote.json`, `viability.json`, `comparables.json`, policy, fees | `price.candidates.json`, or `price.rejected.json` |
| 4. choice | the model, `propose-price` | the candidates, the comparables, the drafts | `price.json` |
| 5. check | `price_candidates.py --check` | `price.json`, the candidates | exit code only |
| 6. approval | a person, in the console | `price.json` with its rationale | `price.approved.json`, an `override` block if they change it, the product record |

Two store-private files drive every number:
`$DAPILOT_STORE_DIR/reference/pricing-policy.json` (required; the scripts
exit 1 without it) and `platform-fees.json` (the store copy when present,
else the plugin copy under `research-comparables/reference/`).

### 4.1 The formula

All of it is in `pricing.py`, imported by both scripts so they cannot
disagree.

```
manufacturing  = setup + grams x filament rate + hours x time rate + post-processing + extras
quote          = manufacturing x (1 + api_commission / 100)        3DAPI's price for one unit
plate unit     = the unit price at max_per_plate from 3DAPI's batch curve  what a plate of them costs each
cost           = plate unit (policy cost_basis full_plate), VAT added if the seller cannot reclaim it
net(P)         = P / (1 + VAT rate) when VAT sits inside the price, else P
fees(P)        = listing fee + payment fixed + P x (transaction pct + payment pct + extra pct)
margin(P)      = net(P) - fees(P) - cost
margin pct     = margin(P) / net(P)
break-even     = (cost + listing fee + payment fixed) / (k - pct),  k = 1 / (1 + VAT rate) or 1
```

The first two lines happen inside 3DAPI and arrive as `unit_price`; dapilot
never sees the terms. Fees and VAT are both functions of the price the
buyer pays, so the break-even is solved, not looked up.

With the policy in force for 3DAPI's own shop (Etsy, EU seller, 21 percent
VAT inside every sale, purchase VAT reclaimable, Offsite Ads at 0):

```
fees(P)    = 0.53 + 0.105 x P
break-even = (cost + 0.53) / 0.7214
```

### 4.2 The policy (decided 2026-09-09 and 2026-09-11)

- **Cost basis** `full_plate`: the unit price at a full plate, the last
  entry of the batch curve on the quote. The shop buys in runs of about
  100 (`stock_units`) and sells from stock, so that is the real unit cost.
  For an object that fits one per plate it equals the one-off quote.
  3DAPI's commission is never backed out: whoever produces on the platform
  pays it, us included. The one-off basis is reported beside it in every
  file (`cost_bases`, `scenarios`) as the road not taken, and
  `--cost-basis` overrides the policy for one run. Section 7b has the
  numbers.
- **Markup** 50 percent on cost.
- **Rounding** to a .99 ending. The markup candidate and the floor round
  up; the market candidates round to the nearest ending.
- **Hard rule**: the sale price is never below the floor, which is
  break-even rounded up, for any author of `price.json`. Enforced by
  `--check`, never by a prompt.
- **Soft rule**: a candidate under `min_margin_pct` (10 percent of net
  revenue) is not offered to the model. A person may still choose it with
  an override, as long as it is at or above the floor.

### 4.3 The candidates

`price_candidates.py` builds at most three:

| Candidate | Value | Rounded |
|---|---|---|
| `markup` | cost x 1.5 | up |
| `median` | `market.median` from `viability.json` | nearest |
| `p75` | `market.p75` from `viability.json` | nearest |

The band is the weighted one when `market.basis` says so (weights are
estimated units per day, section 7c), else the unweighted one. Each
candidate carries its net, fees, margin, margin percent, markup on cost and
position in the band. Then:

- `skipped`: the input is missing (no market block, no median, no p75), or
  the p75 rounds to the same price as another candidate, so the band gave
  no room above it.
- `rejected`: below the floor (`below_floor`) or under the minimum margin
  (`below_min_margin`).
- Exit 0 with one to three candidates in `price.candidates.json`. Exit 2
  when none is left: `price.rejected.json` is written, the skill returns
  `needs_human`, and the item stops there. Exit 1 on error, including a
  `viability.json` with `ok: false`.

The file also carries `p75_sellers`: among the selected comparables priced
at or above the p75, each shop's share of the market weight,
`distinct_named_shops`, `top_share` and `unnamed_share`.

### 4.4 The model's one decision

`propose-price` answers one question: which allowed price will the buyer
who sees ours next to those listings accept. The order of the rules, from
`SKILL.md` and `reference/positioning.md`:

1. `market.thin` is true (fewer than 5 comparables, or fewer than 20 with
   max over min above 3; the script sets it, the model never works it
   out): `markup` when it is a candidate, else `median`, else the sole
   candidate, with the thinness stated. A thin band never picks `p75` over
   an allowed `median`.
2. `median` and `p75` are both candidates: `p75` only when all three hold,
   else `median`.
   (a) the band is weighted, so the p75 is what buyers pay, not what
       sellers ask;
   (b) the drafts show ours at least as good as the median listing: same
       object, no fewer parts, no worse finish;
   (c) the p75 is not one shop's price: `distinct_named_shops` is 2 or
       more, `top_share` under 0.6, `unnamed_share` under 0.2. A null
       `p75_sellers` fails.
3. One candidate: propose it. When it is `p75`, the position is `premium`
   and the rationale names which of (a), (b), (c) held and which failed.

Margin is never a reason: every candidate already clears the floor and the
minimum margin. The price is copied from the candidate, never typed.
`price.json` holds the price, the candidate label, the position (`budget`,
`mid`, `premium`), a three-sentence rationale, the runner-up with one
sentence of why not, the floor and the verdict.

### 4.5 The check and the gate

`price_candidates.py --check` recomputes the floor from the quote and the
policy and compares:

| Exit | Meaning |
|---|---|
| 0 | the price is a candidate, or carries a person's `override: {by, reason}`, and is at or above the floor |
| 3 | below the floor; refused for any author, override or not |
| 4 | not a candidate and no override block, or the currency differs from the candidates' |

On any failure the skill deletes `price.json` and returns `ok: false` with
the errors; it does not adjust the price to pass. The model never writes an
override block.

**The gate (2026-09-17).** The price is a recommendation until a person
approves it in the console, and the design stage requires
`price.approved.json`, so no capture or paid generation runs for a price
nobody confirmed. On the job page the person approves the recommendation,
picks another candidate, or types a price; what a typed price earns comes
from `GET /jobs/{id}/price/preview` (`pricing.py`, no arithmetic in the
browser). `POST /jobs/{id}/price` then:

1. for any price other than the recommendation, rewrites `price.json` with
   an `override: {by, reason, at}` block, the model's proposal kept under
   `proposed`, and runs `--check` on it first. Exit 3 or 4 comes back as
   HTTP 422 with the script's words and nothing is written. A price that
   is not a candidate needs a reason. Returning to the recommended price
   restores the model's proposal with no override left behind;
2. writes `price.approved.json`: price, who, when, `proposal` or
   `override`, floor, unit cost, margin;
3. files the product (`product_record.py`, section 4.8) so it shows in the
   catalogue at once;
4. starts a director for the item, which queues design and what follows.

An item whose candidates were all rejected can be priced the same way:
`--check` then runs against an empty candidate list, so only an override at
or above the floor passes. An item waiting for approval shows as
`price · needs you` and is not picked up by `--resume`.

### 4.6 Worked example: the cat clicker

Eight per plate, aisle 1628, 170 comparables, weighted band, not thin.

```
cost        1.83                               full-plate unit (one-off: 4.97)
break-even  (1.83 + 0.53) / 0.7214 = 3.27      floor 3.99
markup      1.83 x 1.5 = 2.75 -> 2.99          rejected, below the floor
median      8.00 -> 7.99                       net 6.60, fees 1.37, margin 3.40, 52 percent
p75         11.99                              net 9.91, fees 1.79, margin 6.29, 63 percent
```

Both market candidates stand, so the choice between 7.99 and 11.99 rests
on (a), (b) and (c), not on the 2.89 USD of extra margin.

### 4.7 What the chain does not do yet

- **The verdict gates nothing.** `viable`, `thin` and `not_viable` are
  reported, but the price stage runs on any of them; only
  `price.rejected.json` stops an item. A `not_viable` item with a live
  `p75` candidate goes on to design.
- **The `verdict_rule` string in `viability.json` is out of date.** It says
  "one-off break-even"; the code compares the median with the break-even
  and markup price of the policy basis, `full_plate` today.
- **No item has a `price.json` yet.** The two work dirs that reached the
  stage (duck, fidget) hold `price.rejected.json` files written before
  `cost_bases` and `skipped` existed; rerun them before quoting them.
- **Shipping is unknown and Offsite Ads are 0**, which flatters the floor,
  while VAT inside every sale is the worst case and inflates it. Under the
  current numbers the 50 percent markup rarely survives: on the fidget it
  clears break-even by 0.25 USD and is rejected at 3 percent margin. See
  section 8.

### 4.8 The product record

`product_record.py --workdir work/<hash> [--verify-key]` writes
`$DAPILOT_STORE_DIR/products/<sha256>.json` once a price is approved, and
again when the design stage finishes (the server refreshes a record older
than `design.json`). It copies and decides nothing. The record holds the
name, the approved price with its margin, the 3DAPI quote, the media in
listing order, a `storefront` block for the Etsy listing, the API key it
belongs to (`GET /me` through `dapi.py`), and `order_item`: the quote
`publicId` and filament slot count that 3DAPI's `createOrder` needs. 3DAPI
builds the design and the SKU from the quote when an order is placed, so an
order for an end customer needs nothing else to exist on 3DAPI first.

`dapi_listing` in the record is the same product in the shape 3DAPI's
product save expects (`listing`, `manufacture`, `designs`, `skus`, `images`,
`extraFiles`, `complianceFiles`, `materialConfigs`, `imageAssignments`).
It is not sent anywhere: the public API has no product route, files carry
`tempKey: null` and are listed under `pending_uploads`, and `skus` is empty
because a SKU names platform material ids. When that route exists the
record is its request body plus the uploads.

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
both sides until 3DAPI's shipping rates feed the fee file.

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
- Foreign 3mf projects (Creality Print, Orca, Prusa) fail on 3DAPI's slicer
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
weight as estimated units sold per day from the shop's review feed
(section 7c; until 2026-09-14 it was a log of shop sales and favourites,
which records without an estimate still fall back to). Word matching is
whole-word, plural tolerant, hyphen tolerant and accepts digits glued on
the left.

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
10 are listed. A set of our product is ours, read per unit through
`units` (since 2026-10-12, section 7d); only a bundle of different products
counts as not ours, and it is removed by naming the other product.

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

3DAPI's batch curve gives a unit price per quantity up to `max_per_plate`.
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
| 3DAPI unit price | 4.97 | 1.83 |
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

## 7c. Market weight from reviews (decided 2026-09-14)

Until 2026-09-14 the weight behind the band was
`log1p(shop sales) + 0.5 x log1p(favourites) + 1 if sold`, an invented
heuristic with two unfitted constants that mostly measured how old and
big the shop was, because Etsy only exposes a shop's lifetime sales
counter. It is replaced by an estimate of the listing's own sales rate,
from data one public read gives:

- Etsy keeps per-listing sales private, and `getReviewsByListing` returns
  next to nothing for other sellers' listings. But `GET /shops/{id}/reviews`
  is public, newest first, and every review carries a listing id and a
  timestamp. Every review is a sale.
- The feed is capped: offset above 100 returns HTTP 400 "Offset exceeds the
  maximum allowed for anonymous requests", with or without an OAuth bearer
  (measured 2026-09-14 on five shops). So one call per shop reads the
  newest 100 reviews, and the window is twelve months or the span of that
  page, whichever is shorter. For a shop with 2,900 reviews that is about
  three months; for one with 186 it is the full year.

Per shop, in `etsy_search.py sales_estimates`:

```
review_rate    = review_count / transaction_sold_count            lifetime
window         = 365 days, or the span of the page when the page is full and inside the year
shop_rate      = reviews in the window / window / review_rate    units per day
share          = (listing reviews in window + 1) / (shop reviews in window + active listings)
units_per_day  = shop_rate x share
```

The share is add-one smoothed: the prior is an even split of the shop's
recent sales over its active listings, so a listing with no review in the
window keeps half the share of one with a single review instead of
dropping out (the "fallback" decided with the twelve-month window). A shop
with sales but no reviews at all uses lifetime sales over shop age
(`basis: lifetime`); a shop with no sales weighs 0 and stays out of the
band as before. Every record carries the estimate under `sales_est`, plus
`age_days`, `views_per_day` and `favorers_per_day` for a later fit.

`market_weight` is `units_per_day`, so the weighted median is the price at
which half the aisle's units sell. `viability.json` reports
`market.weight_basis` (`units_per_day`, `legacy`, or `mixed` when a file
from before the change is rescored) and `market.weight_concentration`
(the heaviest listing's and the heaviest shop's share of the weight).
Nothing is capped; a band that is one seller's volume is reported, and
the p75 seller-concentration check in `price_candidates.py` is unchanged.

Cost: one review call per distinct shop among the survivors. The shark
read went from 11 to 48 calls with the inventory pass on.

What the shark showed, same 23 comparables, same rules:

| | legacy weight | units per day |
|---|---|---|
| p25 / median / p75 | 4.50 / 7.00 / 13.99 | 3.00 / 4.99 / 10.00 |
| unweighted median | 7.99 | 7.99 |
| heaviest listing's share | 7% | 20% |
| verdict | not_viable | not_viable |
| candidates | p75 13.99 at 36% | p75 9.99 at 15% |

The volume in the aisle sits at the cheap end: the shops that sell most
sharks sell them for 2 to 5 USD, and the 14 USD listings hardly move.
The old weight hid that because a shop with thousands of lifetime sales
weighed the same whether or not its shark listing sold. Two caveats the
numbers make visible: the set-or-bundle residue matters more now (a 2 USD
sea-animals set with three recent reviews is the heaviest listing), and a
zero-review listing from a young shop can rank high on the lifetime basis.
Both are reported in `sales_est`, not corrected. The estimate is noisy
where reviews are sparse: most shark listings had 0 or 1 review in their
window, so the smoothing prior carries much of the band. That is the
market, not the formula, and the cure is a longer observation, which the
API does not offer.

## 7d. The coaster run (2026-09-21): clones, sets and the launch price

`kumikoaster_kakuasa_v.1.2.3mf`, work dir `a5371524`. Research named it a
kumiko lattice coaster (identity medium: the plate thumbnails showed the
lattice, the drafts were washed out, a second plate held unexplained star
parts). Query "kumiko coaster", aisle 1060. Four comparables survived, and
they were the problem:

- All four are listings by one shop, Multiverse3DForge, of one product, a
  set of six coasters with a holder, at 31.00. `market.thin` true,
  `weight_concentration.top_shop_share` 1.0, `p75_sellers` one named shop.
- None of the four carries a single review. The shop sells about 1.27
  units a day over some 200 listings, so each coaster listing's estimate is
  0.006 units a day, which is the add-one smoothing and not evidence of a
  sale. The "market" has no observed demand.
- The verdict was `viable` all the same, because the verdict asks one
  question: is the median above break-even. Markup 3.99 fell under the
  4.99 floor, p75 collided with the median, and the price worker was left
  one allowed candidate, 30.99, which it proposed at a 75.5% margin while
  writing in its log that this was one shop's six-coaster set price and a
  person should override lower for a single. The doubt was in prose,
  where nothing downstream reads it.

Three things follow, in the order they matter.

**Clones and concentration.** Two deterministic rules, no judgement:

1. Collapse listings from the same shop at the same price with
   near-identical titles into one comparable before the band, weights
   summed (a shop's units are already split over its listings, so the sum
   restores the shop's rate). The coaster band then has n 1.
2. Concentration and demand become verdict inputs. Today test (c) guards
   p75 only; the median has no guard. A band where one shop holds 0.6 or
   more of the weight, or whose selected set carries less than a minimum
   total demand (proposed: 0.1 units a day), is `unreliable`, and
   `propose-price` treats it as it treats `thin`: markup when allowed,
   else a human gate. Never the median of one seller.

**The unit of sale.** The pipeline compares per listing; this market sells
per set. Research should record the offer as data: `units` and `extras`
parsed from titles and variants ("set of 6", "6 pcs", "pair", "with
holder"), and two bands, per listing and per unit. The price stage then
decides the offer with numbers: our per-unit cost from the batch curve
times the market's modal set. For the coaster, six at 2.49 is 14.94 in
cost, a set floor near 21.99, 31.00 in the (unproven) market: a 27%
margin, not 76%. The recommended shape of the offer is Etsy quantity
variations on one listing (1, 2, 6), each priced from the same curve, so
a single is a real option at its own price and the set carries the plate
discount. That needs an `offer` block in `price.json` and the design and
copy stages to know they are showing a set. It also leans on the batch
curve, which this quote capped at 2 per plate for a coaster; the curve
question from the fresh-quote finding is on this path too.

**Pricing under the band for the ranking.** Etsy's stated ranking inputs
are title and tag relevance, a listing quality score built from clicks,
favourites and conversion, a temporary boost for new listings, reviews
and shop record, and free shipping in US results. Price enters through
conversion only, so a lower price helps exactly as much as it converts,
and a price far below the band reads as cheap. Position taken: a bounded
launch policy, not a permanent discount. A fourth candidate, `launch`,
10 to 15% under the median, never under p25 and never under the floor,
valid until the listing has N sales or reviews (N to be set in the
policy), after which the price moves to the median. Two Etsy mechanics do
more for the same goal: a Sale on a median list price shows a
strike-through and converts better than a plain lower number, and free
shipping outranks a 10% cut in US search. None of it is measurable from
outside; the evidence will be the shop's own conversion after launch,
which needs the re-pricing step the pipeline lacks (every item is priced
once, at intake). And a market with no observed sales has nobody to
undercut: a lower price does not create buyers.

**Outliers.** Same day, same reasoning from the other side: one listing of
the same design in a gold-compound filament at 100 USD is not the market
cap for a 10 USD print, and with a small n it drags the median. So
`market_stats.py` fences prices with Tukey's rule on the log of price
(with r = max(p75 / p25, 1.5) over the unweighted quartiles, outside
p25 / r^1.5 .. p75 x r^1.5 is out; needs 5 or more priced comparables),
keeps the fenced listings in `comparables.json` marked `outlier`, lists
them under `market.outliers` with title, shop and materials for the
worker to read, and writes `market.iqr` and `market.iqr_ratio`. On the
cat a 49 USD resin keycap bundle went out of a 2.50 to 35 USD band; on
the shark a 1.27 USD mini went out at the low end.

Status (2026-09-21): built. `market_stats.py` collapses clones
(`n_collapsed`, `collapsed` ids on the surviving record, weights summed),
fences outliers, sums demand (`market.demand`), and returns the verdict
`unreliable` with `verdict_reason` when one shop holds 0.6 or more of the
weight, demand is under 0.1 units a day, or fewer than 2 comparables
remain. `price_candidates.py` then lists median and p75 under `rejected`
as `unreliable_band`, so only the markup candidate can stand and no prompt
can argue the band back in; `propose-price` proposes it as
`cost_anchored` and repeats the reason. Rerun on the coaster: 4 listings
fold to 1, demand 0.006 a day, verdict unreliable, markup 3.99 under the
4.99 floor, `price.rejected.json`; the item stops at the gate with the
reason instead of proposing 30.99. The offer block and the launch
candidate stay open: they change the pricing policy and want a decision.

**The unit of sale, built 2026-10-12.** Decided with the shop: the band is
per unit, the offer is the market's pack, 3DAPI's full-plate price stays
the cost basis, and one Etsy sale of a set becomes one 3DAPI order line
with that quantity (createOrder prices N units off the batch curve in
whole plates, so no second quote). What changed:

- `etsy_search.py parse_offer` reads every listing's unit of sale into
  `offer` (`units`, `units_source`, `text`, `extras`) and `unit_price`:
  the chosen variant's properties first ("Set of 4" / "Set of 6" as an
  option), then the title ("set of 6", "Set 6", "6pc", "6 pcs", "3-piece
  set", "x4", "six coasters" when the noun is known, pair, trio, dozen),
  then the description, but only when the title says set, pack or pieces
  without a count. A count followed by a measure (4 x 4 inch, 2x) is not
  a pack. A title that says set with no count anywhere gives `units`
  null. `score` re-reads it on every run, so old files catch up.
- `market_stats.py` builds `market` on `unit_price` (`price_basis:
  per_unit`), keeps the checkout band as `market.per_listing`, lists the
  unknown-count listings under `market.unknown_units`, and when clones
  collapse a head whose title only says "Set" takes the count from a clone
  that says "Set of 6". `offer_of` picks the unit of sale: the modal
  `units` by market weight when its share is at least
  `offer.adopt_share` (pricing policy, 0.5) and at most `offer.max_units`
  (24), else 1; `viability.json` `offer` carries it with the distribution,
  the common extras ("holder") and `offer.market`, the band at the pack
  from the unrounded unit prices. Each scenario gains an `offer` block:
  cost is units x the unit cost, break-even solved on the pack because
  Etsy's fixed fees are paid once per sale. The verdict is judged there.
- `price_candidates.py` prices the pack: every candidate is one purchase
  of `offer.units` with `per_unit` beside it, the floor is the pack's
  break-even, `p75_sellers` reads `unit_price`, and the `margin`
  candidate (lowest price at the minimum margin) now also appears for an
  unreliable band, because 50% markup on a pack lands under the minimum
  margin and the band was not evidence. `--check` reads `offer.units` in
  `price.json`: the model copies it; a person may change it with an
  override block and the floor follows the new pack.
- Downstream: `price.approved.json` and the product record carry `offer`
  (`units`, `per_unit`), the record's `order_item.quantity` is `units`,
  `etsy_listing.py` divides the stock quantity by it, `listing_copy.py`
  puts `offer` in the copy context and `check` refuses a title whose count
  does not read back as the offer, `design_product.py` tells the designer
  the scenes show the whole set, the console shows the price and cost
  "for a set of 6" with the per-unit figure.

Rerun on the coaster: four listings fold to one with `units` 6 (one title
said only "Set"; its clone's "6-piece" settled it), unit median 5.17,
offer median 31.00, pack cost 14.94, floor 21.99, verdict still
unreliable (one shop, no sales), candidates: markup 22.99 rejected under
the minimum margin, median 30.99 rejected as unreliable, `margin` 24.99 at
12.4% proposed, 4.17 a coaster. Fixtures with single-unit comparables are
unchanged to the cent.

Not built: quantity variations (1, 2, 6) on one listing, which need
Etsy inventory writes; a compact plate layout before quoting (3DAPI
measured 2 coasters per plate from the file's 386 mm row, so a set of
six is three plates); the model's own correction of a misread count,
which today is reported in the summary and not fixable from the rules.

## 8. Open decisions

- The VAT share of sales in the pricing policy.
- Whether 3DAPI's own shop pays itself the commission (today: yes, by
  keeping the quote as cost).
- `stock_units` is a single number for the whole shop; per-item batch
  sizes would need a field on the item.
- Shipping: 3DAPI's rates by zone into the fee file, which needs an
  ORDER-scoped key and account approval.
- The six old fixture work dirs that a blanket resume would spend money on.
- Quantity variations (1, 2, 6) on one listing, and the `launch`
  candidate from section 7d. The offer itself is built (2026-10-12).
- Fresh quotes return no batch curve (`max_per_plate` 1, or 2 for a
  coaster); the full-plate cost basis, the stock block and any set price
  depend on it.
