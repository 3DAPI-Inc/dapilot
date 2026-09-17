# Choosing between the candidates

Three prices reach the model at most: the quote plus the policy markup, the
market median, and the market p75. All of them already clear break-even and
the minimum margin, so no choice can lose money on a sale. The question left
is which one the buyer will accept, and that is answered by looking at the
comparables and at our drafts, not at the margins.

`price.candidates.json` says what each candidate is: `how` carries the
number it came from and whether the band was weighted by market weight or
not; `market.basis` says the same for the band as a whole; `p75_sellers`
describes who holds the p75: among the selected comparables priced at or
above the band's p75, each shop's share of the market weight, how many
named shops there are, the largest single share, and the share held by
listings with no shop name. `viability.json` carries `market.thin`, the
script's own call on whether the band is too small or too wide to trust.

## The five tests

Run them in this order. Test 5 is asked first because it changes which
candidates are in play; then the first test that gives a clear answer
decides, except that test 4 is always asked before `p75` is written.

1. **Where does each candidate sit in the band?** `position_in_band` says
   it. A candidate within p25 to p75 is normal. One above p75 needs a reason
   from tests 2 to 4. One below p25 is leaving money on the table unless the
   comparables that sell most are down there too.

2. **What do the listings that actually sell charge?** `comparables.json`
   is already ordered by `market_weight`, heaviest first. If the top two or
   three sit near one candidate, that candidate is the market's answer. A
   band carried by one shop with thousands of sales is that shop's price,
   and undercutting it is the only way in: prefer the lower candidate.

3. **Does ours look like theirs?** Compare the drafts to the comparable
   titles and, if needed, one or two of their images. Same object, same
   finish, no extras: the median. Ours is larger, multi-part, articulated,
   or visibly more detailed than the median listing: the higher candidate is
   defensible. Ours is plainer or smaller: the lower one, and say so.

4. **Does the p75 rule hold?** Asked whenever a `p75` candidate exists,
   and it decides only when `median` is a candidate too. Propose `p75` over
   the median when all three hold, and name each in the rationale:
   (a) the band basis is `weighted`, so the p75 is what buyers pay, not what
       sellers ask;
   (b) test 3 found ours at least as good as the median listing: the same
       object, no fewer parts, no worse finish;
   (c) `p75_sellers` shows several shops: `distinct_named_shops` is 2 or
       more, `top_share` is under 0.6 so no single shop holds most of the
       weight, and `unnamed_share` is under 0.2 so the weight is not held
       by listings nobody can attribute.
   Any of them failing, or `p75_sellers` being null, means the median. A
   premium is not a margin argument: the p75 candidate always earns more,
   which is exactly why it needs (a), (b) and (c) and nothing else counts.
   Market weight is estimated units sold per day, so the band says where
   the aisle's volume is today, and it still rewards incumbents: a new
   listing at p75 is a proposal a person confirms at publish, and the
   rationale should read like one. `market.weight_concentration` in
   `viability.json` says whether one listing or one shop is most of that
   volume; when `top_shop_share` is 0.5 or more, name the shop.

5. **Is the market thin?** Read `market.thin` in `viability.json`; the
   script sets it when fewer than five comparables carry the band or the
   selected prices spread too widely. Never work it out from the counts or
   the prices yourself. When it is true the median is weak evidence: take
   the `markup` candidate, which at least is anchored to our cost, and
   write that the band is thin. When `markup` was rejected, take the
   `median` if it is a candidate, otherwise the sole candidate, and state
   the thinness in the rationale so the person at publish sees it. A thin
   band never picks `p75` over an allowed median.

## Positions and what they mean in the rationale

- **budget**: at or below p25. Only when the sellers there move volume and
  ours is not better than theirs.
- **mid**: between p25 and p75. The default for a like-for-like object.
- **premium**: above p75, or the `p75` candidate itself. With the p75 rule
  held, or with the failed tests named when `p75` is all that is allowed,
  or with a concrete difference named in the rationale: size, parts,
  finish, a use the others lack.

## When there is one candidate

The choice is gone; what is left is whether the price is defensible in the
band, and the rationale is written for the person who confirms it.

- **Only `p75`.** The median and the markup were rejected, which means the
  market sells this object for less than we can make it at the minimum
  margin. Test 3 decides how honest the rationale has to be: if ours is not
  visibly better than the median listing, say so and still propose it,
  position `premium`. Run (a), (b) and (c) anyway and write down which
  held and which failed; a p75 held by one shop is still the only allowed
  price, and the person deciding at publish needs to know that it is one
  shop's price. Do not talk yourself into a premium that the drafts do not
  show, and do not hide a failed test behind a margin.
- **Only `median`.** Propose it, position `mid`, and say what removed the
  others: `p75` skipped for lack of room, `markup` rejected on margin.
- **Only `markup`.** Propose it and say the market band offered nothing
  the policy allows.

## When p75 is missing

`skipped` in `price.candidates.json` says why: the p75 rounded to the same
price as another candidate, so the band gave no room to position above it,
or `viability.json` had no p75. Proposing the median then is not a
downgrade; say that the band gave no room and move on. `p75_sellers` is
not consulted when there is no `p75` candidate.

## What is not a reason

- "Higher margin." Every candidate is profitable; margin is not a
  positioning argument, and neither is "p75 earns more".
- "Round number." The script already rounded.
- "Our cost is high." The buyer does not see our cost.
- "The weighted p75 exists." (a) alone is one test of three.
- "Only three comparables." Thinness is `market.thin`, not a count you
  read off the file.
- Anything about the print process, filament brand or printer.
