# The deep market read

How to name the product, read the market summary, write the word rules that
score every listing, and review the result once. Read this at the step that
needs it, not before. The scoring weights, the word matching and the ladder
are printed by `etsy_search.py --help` and `market_stats.py --help`; this
guide says what they mean for the choices you make, never how to compute
them. You choose words. Every number comes from a script.

## What the scripts already did

`etsy_search.py search --noun` fetches listings through a ladder of price
windows. Etsy returns at most its top 100 by relevance for one query, so the
script asks the same query once per window of price, low to high, and each
window returns its own relevance top 100 in the requested currency. One
query is therefore one call per window, and the windows are the `pages` in
the result, each with its hit rate: the share of listings that carry the
noun in the title and survive the physical and size filters (digital files
and supplies out; stated size more than 2x or less than 0.5x our longest
edge out). `stop_reason` says why the ladder ended. Survivors are then
enriched with shop stats, with the shop's newest reviews (one call per
shop, the source of the sales estimate under `sales_est`), and, when an
OAuth token exists, their inventory, so `price` is the variant closest to
our size where one exists.

`market_stats.py --summary` condenses the survivors into one file you can
read in one pass. `etsy_search.py score` and `market_stats.py --rules` turn
your rules into a relevance score and a market weight per listing, select
the comparables, and compute the weighted band. Nothing in those files is
typed by hand except the words in `rules.json`.

## Naming the product

Four witnesses, and the pictures outrank the name. Look at them in this
order and decide what the object is before you decide what to call it.

- `thumbnails/plate_1.png` and `top_1.png`, when present: the designer's
  own render out of the 3MF, in colour, every part laid out on the bed the
  way it prints. Parts that assemble into one thing sit apart here (the
  octagon fidget shows its two nested shells side by side), so count the
  parts and imagine them together. The top view shows the footprint.
- The drafts: eight grey views of the file as exported, at 45 degree steps.
  Read at least two, from different sides.
- `analysis.json`: size and body count. Two interlocked bodies of 100 mm is
  a fidget; one body of 12 mm is a charm or a miniature.
- `meta.json` `file`: what the uploader called it. Sellers often name files
  after the thing ("octagone+fidget+k2.3mf" says octagon, fidget, and the
  printer it was set up for; ignore printer names, filament brands and
  version tags), and just as often after nothing ("test2.stl",
  "final_v3.3mf", "part A.stl"). A name is corroboration when the pictures
  already show the thing, and a hint of where to look when they are
  ambiguous. It is never the reading on its own: if the pictures do not
  support it, the pictures win, and say so.

**The identity** goes into `rules.json` as `identity`: `object` in a few
words, `confidence`, `witnesses`, `reason`. The levels:

- `high`: the pictures show an unmistakable object and at least one other
  witness agrees (the name says the same, or the size and body count fit
  nothing else). The shark, the duck, the octagon.
- `medium`: the pictures show what it is, but nothing corroborates, or the
  name says something the pictures neither confirm nor deny. A clear phone
  stand called "test2.stl" is medium at best.
- `low`: the pictures are ambiguous, or they contradict the name, or the
  object is a shape that could serve several purposes (a bracket, a
  housing, a mount, a jig). Two readings you cannot choose between is low.
  A low reading still names the product and still runs the read; it says
  in the log, the summary and the console that a person should confirm the
  object before trusting the band.

`witnesses` lists only the sources that supported the reading you chose.
`reason` is one sentence: what settled it, or what is still open.

**The noun** is the one word a seller would not leave out of the title.
Singular; plurals match. It must be the object, not the aisle: "octagon",
"duck", "shark", "clip", not "fidget", "toy", "charm", "gift". A noun that
names the aisle admits the whole aisle and the rules cannot save you; a
noun sellers do not use ("heptagon" for an octagon) leaves nothing to score.
When the file name and the pictures disagree, trust the pictures.

**The query** is what a buyer types: the noun plus one qualifier, two or
three words. "octagon fidget", "duck charm", "cable clip". No brand, no
"3d printed" (it pulls file sellers, which the filter then drops for
nothing), no adjectives a buyer would not use.

**The taxonomy id** is the aisle, and the ladder reads the whole aisle:
every price window of it, in Etsy's relevance order. `etsy_search.py
taxonomy find "<word>"` lists candidates; pick the most specific node that
still contains the product, and go one level up rather than pick a sibling.
A wrong id returns the wrong aisle and the size filter will not catch it.
Known good ids: 1628 (Toys > Stress Balls & Desk Toys) for fidgets, 1799
(Dolls & Miniatures > Figurines) for small figures and charms.

Do not skip the id to "get more listings". Measured 2026-09-11 on the mini
duck: inside aisle 1799 the read is 26 listings, 12 selected, weighted
median 2.57 USD. Without an aisle it is 404 listings, 288 selected, weighted
median 43.35 USD, because silver and gold duck jewellery with no stated
material carries the noun and passes every rule. A small aisle is the whole
market for that object and a thin band is the honest answer; an aisle-less
read is a different product's band. Skip the id only when no node contains
the product at all, and say so in the log.

If the first search exits 2 (nothing kept), run one broader query, once:
keep the noun, drop the taxonomy id or the qualifier. Never a third search.
If the second exits 2 as well, still write `rules.json` so the stage can
close: the noun, an empty `required_any`, and the `excluded`,
`materials_allowed` and `materials_excluded` lists exactly as listed under
"Writing rules.json" below; the scripts then write an empty
`comparables.json` and a `viability.json` with `ok: false`.

## Reading summary.json

Read it top to bottom; every block answers one question.

- `n_fetched`, `n_kept`, `pages`, `stop_reason`: how deep the read went.
  Each page is one price window. Ten kept out of forty is a small category,
  not a bad query. A low hit rate in every window means the query or the
  noun is off; a low hit rate only in the top windows means the expensive
  end of the aisle is a different product, which the words will handle.
- `material_classes` and `materials_top`: what the aisle is made of. The
  classes are a fixed vocabulary; `materials_top` holds the sellers' own
  strings, and those are the words to put in `materials_allowed` and
  `materials_excluded`. Packaging shows up here too ("organza bag", "card",
  "cardstock"): it says how a charm ships, not what it is made of, so never
  exclude it.
- `price` and `price_histogram`: the shape of the aisle before any rule.
  Two clusters usually mean two products (single charms at 2 USD and packs
  at 35 USD) and tell you which words to exclude.
- `with_size_ratio`, `with_weight_g`: how many sellers state dimensions or
  weight. Zero means size cannot separate products and the words must.
- `top_by_market_weight`: the thirty titles whose prices say the most about
  what buyers pay, heaviest first. Read every title. These are the listings
  that will carry the band, so a wrong product here is the one that matters.
  `market_weight` is the listing's estimated units sold per day, from its
  shop's recent reviews split over the shop's listings; `reviews_in_window`
  says how many reviews the listing itself had. `units` is how many of the
  product one purchase brings, read from the title ("Set of 6", "6pc"), the
  chosen variant or the description, and `unit_price` the price of one;
  the band is built on `unit_price`, so a set of six at 31 USD is a 5.17
  USD coaster, and the aisle's usual pack becomes our offer. A row with
  `units` null said "set" without a count and stays out of the band. Read
  the units against the titles: a wrong count on a heavy row is the mistake
  that matters, and it is reported, not corrected by a rule.
- `sample_every_kth`: twenty titles spread across the survivors in id order,
  to see the tail the heavy sellers hide.

After the rules have run, the same file carries `relevance` and
`excluded_by` on each title, `n_selected` and a `relevance_histogram`. That
is the view for the review.

## How words match

The scripts match every word in the lists the same way: case-insensitive,
as a whole word, singular or plural ("duck" finds "ducks", "box" finds
"boxes", "bunny" finds "bunnies"), and a space in a phrase also matches a
hyphen ("3d printed" finds "3D-printed"). A word glued to digits on its left
still matches: "pcs" finds "10pcs" and "5pcs", so one entry covers every
pack size. Prefixes do not match: "wood" does not hit "wooden", "metal"
does not hit "metallic", "pla" does not hit "plastic". List the forms you
mean. Blank entries are dropped, so an accidental empty string changes
nothing.

## Writing rules.json

One file, six fields, every list lowercase. There is no threshold to write:
the script selects at its own default, and the only way to move a listing
in or out is a word.

- `noun`: the word from step 2. The scorer gives most of its credit to a
  title that carries it.
- `required_any`: words that name the mechanism or the kind, any one of
  which earns a fixed share of the score, matched in title or tags. A
  listing with none of them loses that share; on its own that drops only a
  listing that also states no material and no size, but together with an
  excluded word it decides. That is the lever: `required_any` settles the
  borderline cases, not the clear ones. Name what makes the product this
  product: "twist",
  "swirl", "passthrough" for a twisting fidget; "charm", "mini",
  "miniature", "figurine" for a small figure. Never the aisle word
  ("fidget", "toy"): every listing has it, so it decides nothing. An empty
  list earns the share for everyone, which is right only when the noun
  alone is the product.
- `excluded`: words that name the neighbouring products a buyer would see
  for the same query: "spinner", "cube", "keychain", "pendant",
  "articulated", "custom". Never "set of", "bundle", "pack of" or "pcs":
  a set of our product is ours, read per unit through `units`; a bundle of
  other products goes by the other product's name. Matched in title and
  tags. A hit is a penalty, not a veto: it removes one share of the score.
  A listing with an excluded word stays in only when nearly everything else
  is in its favour: the noun, and at most one of a required word, a stated
  allowed material and a plausible size missing. Missing two of those on
  top of the hit puts it out. That is deliberate, because tags are noisy,
  and it is what the review is for.
- `materials_allowed`: the sellers' strings for how we make it, from
  `materials_top`: "pla", "petg", "asa", "tpu", "resin", "epoxy resin",
  "3d printed", "plastic". A match earns a share; no match loses it, which
  on its own drops nobody who states no material.
- `materials_excluded`: a veto. A listing that carries one of these in its
  materials, title or tags scores zero, whatever else it has. Use it for
  what the object is made of in another market: "metal", "steel",
  "stainless", "brass", "copper", "titanium", "wood", "wooden", "leather",
  "glass", "ceramic", "porcelain", "polymer clay", "clay". Never for
  packaging or paint.
- `taxonomy_id`: the id from step 2, or null.
- `notes`: one sentence naming the product and the neighbours it is not.

## Worked example: the octagon fidget

Two nested twisted pieces, 104 mm, PLA. Noun "octagon", query "octagon
fidget", taxonomy 1628. On the single-window read of 2026-09-11 the search
kept ten listings. The summary showed metal, leather and printed listings,
prices from 2.25 to 360, and eight sellers stating size. Heaviest titles: an
Infinity Twist wobble fidget with 6,500 sales, a 360 USD steel top, a
magnetic slider, a keychain, an infinity cube, a swirl-and-stretch octagon,
a spinner with a handle, a 3-piece passthrough set.

First rules: `required_any` ["fidget", "twist", "swirl", "passthrough",
"pass through", "infinity"], `excluded` ["spinner", "keychain", "pendant",
"slider", "clicker", "magnetic", "cube"] (the run of 2026-09-11 also had
"set of", "piece set" and "bundle", which the unit of sale made wrong),
metals and leather under `materials_excluded`. Result: 5 selected. The
review found 3 of the 5 were a cube, a spinner and a 3-piece set: fewer
than 10 listed and more than 1 wrong, so tighten. Each wrong one carried
the noun, "fidget" and "3d printed", so its one excluded word was not
enough to drop it. The mistake was "fidget" in `required_any`, the aisle
word: it had earned the required share for everyone.

Tightened once: `required_any` ["twist", "swirl", "passthrough", "pass
through", "spiral", "vortex", "wobble", "stretch"]; nothing else changed.
Now the cube and the spinner lack a required word and carry an excluded
one, so they fall out. Result: 3 selected (the swirl octagon, the Infinity
Twist, the 3-piece passthrough set with one sale and almost no weight),
verdict viable. The set is not a residue: it is three of our product, so it
is read as `units` 3 at a third of its price and belongs in the band. (Until
2026-10-12 a set counted as not ours and was kept only because no word
removed it; the band then compared its set price to our single.)

## Worked example: the mini duck

One body, 12 mm, printed. Noun "duck", query "duck charm", taxonomy 1799.
The single-window read kept fifteen listings. The summary showed resin
miniatures at 2 to 3 USD from two shops with thousands of sales, a polymer
clay family at 45, a 5-pack at 35, a clay keychain at 38, an articulated
printed duck; nobody stated size or weight. `materials_top` was resin,
epoxy resin, paint, and packaging strings.

Rules: `required_any` ["charm", "mini", "miniature", "tiny", "pocket hug",
"figurine"], `excluded` ["family", "keychain", "articulated", "articulado",
"custom"] (the run of 2026-09-11 also excluded "set of", "pcs", "pack of"
and "bundle"; since 2026-10-12 a 10-pack of charms is ten charms at a tenth
of the price, and "family" names the bundle of different animals),
`materials_allowed`
with resin and epoxy resin, `materials_excluded` with polymer clay, clay,
ceramic, porcelain, metal, wood, glass. Result: 12 of 15 selected, the clay pieces vetoed to
zero. The review found 2 of 12 that are not our product (the 5-pack, the
articulated duck), each in through one penalty with everything else in
its favour: 10 or more listed and not more than 4 wrong, so the rules stood.
Verdict not_viable against a unit cost of 2.59; that is the market, not the
rules.

## What a bad rule looks like

- The aisle word in `required_any` ("fidget", "toy", "gift", "decor"): it
  earns the share for everyone and the band is the whole aisle.
- A material veto on packaging or finish ("card", "paint", "glitter"): it
  removes the best sellers of our own kind.
- An adjective form missing ("wood" without "wooden"): the wooden version
  stays in at full relevance.
- A veto that names our own material class ("plastic" under excluded
  because a metal listing said "no plastic"): everything scores zero.
- A qualifier in `excluded` that our own product also carries ("mini" for a
  charm): it penalises every genuine comparable at once.
- Rules written from the drafts alone without reading `materials_top` and
  the heaviest titles: the words will not match how the aisle talks.

## The once-only review and tightening

After the two scripts have run, read `top_by_market_weight` in the
refreshed summary. Entries at or above the threshold the script printed
are the comparables, heaviest first; the rest show what the rules removed
and why (`excluded_by`). Look at the first twenty comparables against the
drafts and count the ones that are a different product. A set of our
product is ours, counted per unit through `units`; only a bundle of other
products is different, and it goes by naming the other product, never by
"set", "pack" or "pcs". More than 4 of 20 means the rules are
wrong; with fewer than 10 comparables listed, more than 1 does.

Tighten once, with the smallest edit that names the mistake: replace an
aisle word in `required_any` with mechanism words; add the missed neighbour
to `excluded`; add the missed material form to `materials_excluded`. Those
three lists are the only levers. Then rerun `score` and `market_stats.py
--rules` once. Whatever comes back is the answer; the residue goes in the
log, never into a third pass or a hand edit of `comparables.json`.

If the first pass selected nothing (`market_stats.py` exits 2 with listings
kept), the one edit removes words instead: `rejected_sample` shows the
highest near misses and `excluded_by` says which rule hit them.

Heavy sellers that the rules removed are worth one sentence in the log: a
shop with thousands of sales is a competitor, not a market, and if the
whole band is one shop, say so.

## A thin band

`viability.json` carries `market.spread_ratio`, the widest selected price
over the narrowest, and `market.thin`, which the script sets when fewer
than five comparables carry the band or the spread is wide. Both are
reported, in the log and in the result summary, exactly as printed. A thin
band is a fact about the market, not a fault in the rules: do not loosen
the words to make it wider, and do not tighten them to make it narrower.
`propose-price` reads the same flag and leans on cost when it is set.

## Quota

The search prints `quota.remaining_today`. A deep read costs one search
call per price window, one batch call per hundred survivors and one
inventory call per survivor with variants; a typical item is a few dozen to
a couple of hundred calls. Below 500 remaining, do not run the broader
second search: say so in the log and let the next item have the calls. Do
not pass `--limit` or the stop flags; the defaults are the agreed depth.
