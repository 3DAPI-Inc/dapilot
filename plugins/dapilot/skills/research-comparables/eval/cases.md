# research-comparables eval cases

Scripts run by hand against the live Etsy API with an OAuth token, so the
inventory pass was on, under the store pricing policy of 2026-09-09 (cost
basis quote, Etsy, EU seller, 21% VAT inside the sale price, 50% markup,
10% minimum margin). The `expected.*.deep.*` files are what those runs
produced, copied from the scratch work dirs with the scratch prefix
rewritten to `work/<hash>` and the repo prefix dropped; every path inside
`viability.json` is relative to the work dir. Prices move and the ladder
reads deeper than the single-window run of 2026-09-11 that the counts below
come from, so a rerun matches on shape, on the kind of listing selected, on
the review count and on the verdict, not on the cents or the fetch counts.

Superseded: `expected.octagon-fidget.comparables.json`,
`expected.octagon-fidget.viability.json` and
`expected.octagon-fidget.price.candidates.json` (no `.deep.` infix) are from
the old method of 2026-09-09, where the model judged listings one by one
and wrote `comparables.json` by hand. They are kept so the hand-written
shape stays covered by `market_stats.py` (it still reads it without
`--rules`) and can be removed by a person once that path is retired. The
`.deep.` infix can go with them.

Reference files regenerated 2026-09-11 from ladder runs inside the
taxonomy aisle (six windows fetched and three skipped for the fidget, nine
for the duck). `summary.json` lists selected comparables only under
`top_by_market_weight` (each with `selected: true`) once rules exist, with
the heaviest rejects under `top_rejected_by_market_weight`. No rules file
carries a threshold.

Weights changed 2026-09-14: `market_weight` is now the listing's estimated
units sold per day from its shop's review feed (`sales_est` on every
record), and the old log-of-shop-sales formula applies only to records
without an estimate. Every expected file here predates that and carries
legacy weights; a rerun with the review pass on gives a lower band on the
fidget and the duck wherever the volume sits at the cheap end, so compare
`market.weight_basis` and the shape, not the band numbers, until the
fixtures are regenerated.

Naming: `expected.<fixture>.deep.<work dir path with / as .>`. Fixtures:
`octagon-fidget` is `work/56d80708...`, `duck-charm` is `work/2936c775...`,
`no-noun-match` is the fidget dir with a wrong noun.

Pages are price windows: the search asks the query once per window of
price and each window returns Etsy's relevance top 100 for that range, so
`pages` in the result lists windows, each with its own hit rate, and
`stop_reason` says why the ladder ended. The skill passes `--noun` and
never `--limit`.

## 1. Happy path with one tightening: octagon passthrough fidget

Before: `work/56d80708.../` with `source.3mf`, `meta.json` (file
"octagone+fidget+k2.3mf"), `analysis.json` (2 bodies, 100.8 x 44.8 x
104.0 mm), `quote.json` (unit 5.71 USD, 1 per plate, `model_g` 40.9),
`renders/draft_0*.png`. No `comparables.json`.

Judgement 1, name: two nested twisted cones; the filename says octagon and
fidget. Noun `octagon`, query `octagon fidget`, taxonomy 1628 (Toys & Games
> Toys > Stress Balls & Desk Toys), one level above the spinner node.

Script: `etsy_search.py search "octagon fidget" --noun octagon --taxonomy-id 1628 --analysis analysis.json --currency USD --out research/etsy.json`
Expected: exit 0, one `pages` entry per price window with its hit rate,
`stop_reason` set, `inventory_read: true`, the counts printed on stdout.
The single-window run fetched 42 and kept 10; the ladder keeps more and
the log records whatever the script printed.

Script: `market_stats.py --workdir . --summary research/summary.json`
Expected: exit 0, stdout notes it stopped after the summary. Material
classes led by 3d_printed with metal and leather present; prices from a
few dollars to hundreds (a steel top); some sellers state size and weight;
the heaviest title an Infinity Twist wobble fidget with thousands of sales.

Judgement 2, rules, first pass:
`expected.octagon-fidget.deep.research.rules.pass1.json` (`required_any`
includes "fidget", the aisle word). No threshold is written; the script's
default applies. Scripts `etsy_search.py score` then
`market_stats.py --rules research/rules.json --summary research/summary.json`:
exit 0; on the ladder run 5 selected of 9.

Judgement 3, review: fewer than 10 comparables listed and 3 of them are
other products (an infinity cube, a spinner with handle, a 3-piece set),
each in with one excluded word because "fidget" earned the required share
for everyone. More than 1 wrong: tighten once, by words only.
`required_any` becomes the mechanism words ["twist", "swirl",
"passthrough", "pass through", "spiral", "vortex", "wobble", "stretch"];
nothing else changes. Reference answer
`expected.octagon-fidget.deep.research.rules.json`.

Scripts rerun once. Expected: `score` prints the new selected count;
`market_stats.py` exit 0, `comparables.json` as
`expected.octagon-fidget.deep.comparables.json`: records ordered by
`market_weight` descending, each with a `shop` block; on the single-window
run 3 selected, ordered by market weight (the Infinity Twist 10.49, the swirl octagon 5.49, the 3-piece passthrough set 15.00,
the Infinity Twist 10.49) and 7 rejected (the metal, leather and magnetic
pieces at relevance 0). `viability.json` as
`expected.octagon-fidget.deep.viability.json`: `market.basis` weighted,
`market.weighted` and `market.unweighted` both present, `spread_ratio` and
`thin` printed (3 comparables is under five, so `thin` is true), unit cost
5.71, break-even 8.65, verdict `viable`, per-gram from the listings that
state weight, shipping unknown. Summary as
`expected.octagon-fidget.deep.research.summary.json` with `relevance` and
`excluded_by` on every title. The set is the one allowed exception: it is
a set of our product, so it counts as not ours, but no word removes it
without removing the genuine passthrough listings, and with 1 wrong of 3
the count rule is satisfied anyway. The log says so. Two consecutive runs
of the two scripts give byte-identical `comparables.json` and
`viability.json`.

Script (price stage, same fixture): `price_candidates.py --workdir .`
Expected: `expected.octagon-fidget.deep.price.candidates.json`. Floor 8.99;
one candidate, `p75` at 11.99 with 24.3% margin; `markup` 8.99 and
`median` 8.99 rejected under the 10% minimum margin; `p75_sellers` names
one shop holding the whole weight above the p75.

Result line: `{"ok": true, "cached": false, "stage": "research", "outputs": ["comparables.json", "viability.json"], "needs_human": null, "reason": null, "summary": "octagon fidget: 3 of 10 selected after one tightening, weighted median 10.48 USD, p75 10.48, thin, unit cost 5.71, break-even 8.65, viable"}`

After: `research/etsy.json`, `research/etsy.full.json`,
`research/summary.json`, `research/rules.json`, `comparables.json`,
`viability.json`, one paragraph in `log.md`.

## 2. Happy path without tightening: mini duck charm

Before: `work/2936c775.../` with `meta.json` (file "Mini+Duck+Titan3D+C.3mf"),
`analysis.json` (1 body, 12.0 x 9.65 x 8.3 mm), `quote.json` (unit 2.59
USD, `model_g` 0.40), drafts. No `comparables.json`.

Judgement 1: noun `duck`, query `duck charm`, taxonomy 1799 (Art &
Collectibles > Dolls & Miniatures > Figurines).

Script: search as in case 1 with those values. Expected: exit 0, one
`pages` entry per window, `stop_reason` set. The single-window run kept 15
of 26; the ladder over "duck charm" measured about 700 listings before the
filters. Summary: resin leads the material classes, with ceramic and
plastic present; a low cluster of single charms at 2 to 3 USD and a high
cluster of packs and clay families; nobody states size or weight;
`materials_top` led by resin and epoxy resin, with organza bag and card as
packaging.

Judgement 2: `expected.duck-charm.deep.research.rules.json`. The `excluded`
list carries "pcs" once, which covers "5pcs" and "10pcs" because a word
glued to digits on its left still matches. Scripts: `score` prints the
selected and excluded counts; `market_stats.py` exit 0;
`comparables.json` as `expected.duck-charm.deep.comparables.json` (on the
single-window run 12 selected, 3 rejected: the clay family, the clay
keychain and the set of 2, all vetoed by "polymer clay"); `viability.json`
as `expected.duck-charm.deep.viability.json`: weighted band with median
2.57 and p75 2.58 on that run, `thin` true because the selected prices
spread more than 3x from the 1.81 charms to the 34.82 pack, unit cost 2.59,
break-even 4.32, verdict `not_viable`, per-gram null.

Judgement 3, review: 10 or more comparables listed and 2 of them are other
products (a 5-pack and an articulated duck, each through one penalty). Not
more than 4: the rules stand, no rerun. The 5-pack is a set, counted as
not ours, and it is under the count rule, so it needs no exception.

Script (price stage): `price_candidates.py --workdir .` exits 2 and writes
`price.rejected.json` as `expected.duck-charm.deep.price.rejected.json`:
floor 4.99, `markup` 3.99 and `median` 2.99 below the floor, `p75` skipped
because 2.79 rounds to the same price as the median.

Result line: `ok: true`, both outputs, `needs_human: null`, summary
"duck charm: 12 of 15 selected, weighted median 2.57 USD, p75 2.58, thin,
unit cost 2.59, break-even 4.32, not_viable".

## 3. Hostile: nothing survives

Before: the fidget dir with a noun sellers do not use. Search
`"octagon fidget" --noun heptagon --taxonomy-id 1628`.

Expected: the search exits 2 with `kept: 0`, every listing dropped as
`noun_missing`, and still writes `research/etsy.json` as
`expected.no-noun-match.deep.research.etsy.json`. The skill runs one broader
query, once. When that also exits 2, it writes `rules.json` with the noun
and the guide's default lists, then `score` (exit 2, `n: 0`) and
`market_stats.py --rules` (exit 2), which write `comparables.json` as
`expected.no-noun-match.deep.comparables.json` (`n_scored` 0, empty list)
and `viability.json` as `expected.no-noun-match.deep.viability.json`
(`ok: false`, "no comparables with a price").

Result line: `ok: true`, outputs both files, `needs_human: "no priced
comparables for octagon fidget; skip the model or supply a query"`.

## 4. Hostile: rules select nothing

Before: case 2 after the search, with a hand-edited `rules.json` carrying
`min_relevance` 0.99. The skill never writes that field; this is a hostile
input the script still honours.

Expected: `score` prints a selected count of 0; `market_stats.py --rules`
exits 2, `comparables.json` has `n_scored` 15, `n_selected` 0,
`rejected_count` 15 and a `rejected_sample` of 10; `viability.json` is
`{"ok": false, "reason": "no comparables with a price", "n_comparables": 0,
"mode": "rules", "n_scored": 15, "min_relevance": 0.99}`. The skill uses
its one rerun to remove words, guided by `rejected_sample`; it does not
touch the threshold. If that exits 2 again, it finishes as case 3.

## 5. Hostile: quote without metrics

Before: `quote.json` from before 2026-09-08, no `metrics` block.

Expected: `market_stats.py` exits 0; `ours.model_g`, `ours.parcel_g` and
`ours.per_gram_at_market_median` are null; `market.per_gram` still comes
from listings that state a weight. Not a failure.

## 6. Missing environment

Before: `ETSY_KEYSTRING` unset, or `DAPILOT_STORE_DIR` unset.

Expected: the search exits 1 with
`{"ok": false, "error": "ETSY_KEYSTRING and ETSY_SHARED_SECRET must be set (or pass --env store/.env)"}`;
`market_stats.py --rules` without the store dir exits 1 with
`{"ok": false, "error": "DAPILOT_STORE_DIR is not set; the pricing policy lives at $DAPILOT_STORE_DIR/reference/pricing-policy.json"}`.
The skill returns `ok: false` with that line as `reason`. With the store
dir unset but the key present, the search itself still runs: the inventory
pass is skipped and the taxonomy is cached under `~/.cache/dapilot/`.

## 7. Cached rerun

Before: case 1 or 2 after completion.

Expected: the first step finds `comparables.json` and prints
`{"ok": true, "cached": true, "stage": "research", "outputs": ["comparables.json", "viability.json"], ...}`
without running a script or spending quota.
