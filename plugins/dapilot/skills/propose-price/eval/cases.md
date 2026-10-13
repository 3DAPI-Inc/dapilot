# propose-price eval cases

All under the store pricing policy of 2026-09-09: cost basis quote, Etsy,
EU seller, 21% VAT inside the sale price, 50% markup, 10% minimum margin,
.99 rounding. The policy is private, so the expected files here are the
reference for its values on that date; change the policy and regenerate.

The synthetic cases carry their inputs under `fixtures/<case>/`: the real
fidget `quote.json` (unit 5.71), a constructed `viability.json` and
`comparables.json`, and for the p75 case a minimal `research/etsy.full.json`
that supplies the shop names the older rules-shape comparables lack. Copy
a fixture dir somewhere, add drafts, run the script there. The synthetic
`viability.json` files predate `market.thin`; the skill reads a missing
flag as not thin and says so, and each of them has eight comparables inside
a 3x spread, so a regenerated file carries `thin: false`.

`p75_sellers` is read as the script writes it: the shops holding weight at
or above the band's p75 with each one's `weight_share`,
`distinct_named_shops`, `top_share` and `unnamed_share`. Test (c) holds at
2 or more named shops, `top_share` under 0.6 and `unnamed_share` under 0.2.

Superseded: `expected.octagon-fidget.price.rejected.json` is from the
hand-shape research of 2026-09-09, when the fidget band was unweighted and
nothing cleared the floor. The deep read replaced it with case 1 below; the
file is kept until a person removes it.

## 1. Sole median candidate, thin band, p75 skipped: octagon passthrough fidget (real)

Before: `work/56d80708.../` after the deep research read of 2026-09-11:
`quote.json` (unit 5.71), `comparables.json` (3 selected, ordered by
market weight: the Infinity Twist at 10.49 with thousands of sales, the
swirl octagon at 5.49, a 3-piece passthrough set at 15.00 from one shop
with one sale), `viability.json` (`market.basis` weighted, cumulative-mass
median 10.48 and p75 10.48, `market.thin` true, verdict `viable`), the
drafts. No `price.json`.

Script: `price_candidates.py --workdir .`
Expected: exit 0, `price.candidates.json` as
`expected.octagon-fidget.deep.price.candidates.json`. Floor 8.99. One
candidate, `median` 9.99 from 10.48, margin 0.97 (11.7%). `markup` 8.99
rejected `below_min_margin` (3.3%). `skipped`: `p75` 10.48 rounds to 9.99,
the same price as the median candidate. `p75_sellers` over the listings at
or above 10.48: two named shops, `top_share` 0.875, `unnamed_share` 0.0,
so (c) would fail; kept for the record.

Judgement: `market.thin` is true, `markup` is not a candidate and `p75` is
skipped, so the median is the sole allowed price. Test 3: ours is the same
two-piece twisted object as the Infinity Twist that holds most of the
weight, no fewer parts, no worse finish. Propose `median` 9.99, position
`mid`, runner-up `markup` 8.99 rejected on margin. The rationale states
that (a) and (b) held and (c) failed at 0.875 for one shop, and that the
band is thin. Reference in `expected.octagon-fidget.deep.price.json`.

Script: `price_candidates.py --workdir . --check`
Expected: exit 0, `ok: true`, margin 0.97.

Result line: `{"ok": true, "cached": false, "stage": "price", "outputs": ["price.json"], "needs_human": null, "reason": null, "summary": "9.99 USD, median candidate, mid, margin 12%, thin band, p75 skipped"}`

After: `price.candidates.json`, `price.json`, one paragraph in `log.md`.
Render dispatches for this item; the price is confirmed at publish.

## 2. Happy path: synthetic viable market, unweighted band

Before: `fixtures/synthetic-viable/`: the fidget quote, the hand-shape
`comparables.json` with every price multiplied by 2.2 (median 14.28),
`viability.json` verdict `viable` with no `basis` (a pre-weighting file,
read as unweighted) and no `market.thin`, the drafts.

Script: `price_candidates.py --workdir .`
Expected: exit 0, `price.candidates.json` as
`expected.synthetic-viable.price.candidates.json`. `market.basis`
`unweighted`. Two candidates: `median` 13.99, margin 3.85 USD (33.3%),
within p25 to p75; `p75` 19.99 (from 20.34, "unweighted band"), margin
8.18 USD (49.5%). `markup` 8.99 rejected `below_min_margin`: the markup
candidate's margin does not depend on the market, so under this policy it
is rejected whenever the quote is small. `p75_sellers` names PolyBrain and
ErnicShop at or above 20.34, `distinct_named_shops` 2, with PolyBrain's
`weight_share` above 0.8 so `top_share` is over 0.6.

Judgement: not thin (no flag, said so in the log). Both market candidates
exist, so (a), (b), (c) decide: (a) fails, the band is unweighted, and (c)
fails too, one shop holds most of the weight above the p75. The median.
Reference answer in `expected.synthetic-viable.price.json`: position `mid`,
rationale names the volume sellers near the median, that ours is the same
object, and that (a) and (c) failed.

Script: `price_candidates.py --workdir . --check`
Expected: exit 0, `ok: true`, margin 3.85.

Result line: `{"ok": true, "cached": false, "stage": "price", "outputs": ["price.json"], "needs_human": null, "reason": null, "summary": "13.99 USD, median candidate, mid, margin 33%"}`

## 3. Hostile: the model types its own price

Before: case 2 up to step 3, but `price.json` says 12.49.

Script: `--check` exits 4: "price 12.49 is not one of the candidates
[13.99, 19.99] and price.json has no override block". The skill deletes
`price.json` and returns `ok: false` with that line as `reason`. The ledger
retries once with the reason in the brief.

## 4. Hostile: below the floor, with an override

Before: `price.json` with 3.99 and `override: {"by": "supervisor", "reason": "clearance"}`.

Script: `--check` exits 3: "below the hard floor 8.99; refused for any
author". This is the one rule no author can pass. The skill never writes an
override block, so reaching this case means a person did, and the answer is
still no.

## 5. Cached rerun

Before: case 2 after completion.

Expected: `{"ok": true, "cached": true, "stage": "price", "outputs": ["price.json"], ...}`
with no script run.

## 6. Missing policy

Before: `DAPILOT_STORE_DIR` unset, or no `reference/pricing-policy.json` in it.

Expected: the script exits 1 naming the path; the skill returns `ok: false`
with that line. Nothing is written.

## 7. p75 chosen: synthetic weighted band, p75 clears everything

Before: `fixtures/synthetic-weighted-p75/`: the fidget quote; a rules-shape
`comparables.json` with 8 synthetic listings (prices 8.99 to 22.99, the
three at or above 19.50 from PolyBrain, ErnicShop and 3DMOLDFARMLLC, each
with hundreds to thousands of sales); `viability.json` verdict `viable`,
`market.basis` `weighted`, weighted p25 11.20, median 14.28, p75 19.50;
`research/etsy.full.json` with the shop names, since this older
rules-shape file has no `shop` block on its records; the drafts.

Script: `price_candidates.py --workdir .`
Expected: exit 0, `expected.synthetic-weighted-p75.price.candidates.json`.
`median` 13.99 (33.3%), `p75` 19.99 (from 19.50, "weighted band", 49.5%,
`above_p75` after rounding), `markup` 8.99 rejected. `p75_sellers`: three
named shops at or above 19.50 with weight shares near a third each,
`distinct_named_shops` 3, `top_share` under 0.4, `unnamed_share` 0.0.

Judgement: not thin. (a) weighted, (b) ours is the same two-piece cone as
the median listing, (c) three shops share the p75 with no one holding
most of it. All three hold: propose `p75`, position `premium`. Reference
answer in `expected.synthetic-weighted-p75.price.json`; the rationale names
all three.

Script: `--check` exits 0, margin 8.18.

Result line: `{"ok": true, "cached": false, "stage": "price", "outputs": ["price.json"], "needs_human": null, "reason": null, "summary": "19.99 USD, p75 candidate, premium, margin 50%"}`

## 8. Median chosen: (c) fails, one shop holds the p75

Before: `fixtures/synthetic-weighted-oneshop/`: the same band as case 7,
but a hand-shape `comparables.json` (shop names on the records) where every
listing at or above 19.50 is PolyBrain.

Script: `price_candidates.py --workdir .`
Expected: exit 0, `expected.synthetic-weighted-oneshop.price.candidates.json`.
Same three prices and margins as case 7; `p75_sellers` one named shop,
`distinct_named_shops` 1, `top_share` 1.0, `unnamed_share` 0.0.

Judgement: not thin; both market candidates exist. (a) and (b) hold, (c)
fails: the p75 is one incumbent's price. Propose `median` 13.99, position
`mid`, `runner_up` p75 with "(c) did not hold". Reference answer in
`expected.synthetic-weighted-oneshop.price.json`.

Script: `--check` exits 0, margin 3.85.

## 9. p75 skipped: the band is too tight

Before: `fixtures/synthetic-weighted-skipped/`: weighted band with median
14.28 and p75 14.40, both rounding to 13.99.

Script: `price_candidates.py --workdir .`
Expected: exit 0, `expected.synthetic-weighted-skipped.price.candidates.json`.
One candidate, `median` 13.99; `skipped` carries `p75` with the reason
that 14.40 rounds to 13.99, the same price as the median candidate.
Whatever `p75_sellers` holds is not consulted: there is no `p75` candidate.

Judgement: one candidate; the median, position `mid`, `runner_up` null,
and the rationale says the band gave no room above it.

Hostile variant: `price.json` with 19.99 and `candidate: "p75"`. `--check`
exits 4: "price 19.99 is not one of the candidates [13.99]". Skipped is not
a candidate.

## Note, 2026-10-12: the unit of sale

`price_candidates.py` prices one purchase of `offer.units` printed units
(`viability.json` `offer`, the market's modal pack): cost is units x the
unit cost, the floor is the pack's break-even, every candidate carries
`per_unit`, and `price.json` carries `offer.units`, which `--check`
compares to the market's unless an override block changes it. The `margin`
candidate now also stands for an unreliable band. The fixtures here have
no `offer` and so sell singles; their candidates are unchanged to the cent
(rerun 2026-10-12). On the kumiko coaster (`work/a5371524...`): units 6,
cost 14.94, floor 21.99, markup 22.99 rejected under the minimum margin,
median 30.99 rejected as unreliable, `margin` 24.99 at 12.4% the one
candidate; a `price.json` with `offer.units` 1 and no override is refused,
and with an override at 5.99 passes against a 4.99 floor for one.
