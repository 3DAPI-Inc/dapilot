import type { MarketBand, Viability } from '@/api/jobs';

/*
  What the band is made of, said before the numbers are trusted: whether it is
  evidence at all (unreliable: one shop's price, no observed demand, too few
  listings once clones are folded), what was fenced out of it, and how much
  demand it represents. All of it is the script's arithmetic; the page only reads.
*/

const money = (n: number) => `$${n.toFixed(2)}`;

export function BandVerdictNote({ v, className }: { v: Viability; className?: string }) {
  if (v.verdict !== 'unreliable') return null;
  return (
    <p className={`max-w-[62ch] font-sans text-sm leading-relaxed text-destructive ${className ?? ''}`}>
      This band is not evidence: {v.verdictReason ?? 'see viability.json'}. Only the cost-anchored price can be proposed from it.
    </p>
  );
}

export function BandComposition({ m, className }: { m: MarketBand; className?: string }) {
  const outliers = m.outliers ?? [];
  const collapsed = m.n_collapsed ?? 0;
  const d = m.demand;
  if (outliers.length === 0 && collapsed === 0 && !d) return null;
  return (
    <div className={className}>
      <p className="max-w-[62ch] font-sans text-xs leading-relaxed text-slate/80">
        {collapsed > 0 ? `${collapsed} clone listing${collapsed === 1 ? '' : 's'} (same shop, same price, same title) folded into one comparable. ` : ''}
        {m.iqr != null ? `Interquartile range ${money(m.iqr)}${m.iqr_ratio != null ? ` (${m.iqr_ratio.toFixed(2)}x)` : ''}. ` : ''}
        {d?.units_per_day != null
          ? `Estimated demand ${d.units_per_day.toFixed(2)} units a day across the band; ${d.listings_with_reviews} of ${d.n} listing${d.n === 1 ? '' : 's'} carry any review.`
          : ''}
      </p>
      {outliers.length > 0 ? (
        <ul className="mt-2 max-w-[62ch] font-sans text-xs leading-relaxed text-slate/80">
          {outliers.map((o) => (
            <li key={`${o.listing_id ?? o.url}`}>
              Fenced out ({o.side}): {money(o.price)}
              {o.url ? (
                <>
                  {' '}
                  <a href={o.url} target="_blank" rel="noreferrer" className="text-navy underline-offset-4 hover:underline">
                    {o.title ?? 'listing'}
                  </a>
                </>
              ) : (
                ` ${o.title ?? ''}`
              )}
              {o.shop ? ` by ${o.shop}` : ''}
              {o.materials.length ? ` (${o.materials.slice(0, 3).join(', ')})` : ''}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
