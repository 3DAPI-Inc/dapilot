import type { Quote } from '@/api/dapi';
import { money } from '@/lib/format';
import { cn } from '@/lib/utils';

/*
  Batch pricing, as a table.

  It used to be one line of "5 × $9.80 · 10 × $9.10", which hid the only thing
  a batch quote is for: unit price falls as plates fill, and it stops falling
  somewhere. The table names that floor and shows how far each quantity is from
  it, so "how many do I print" can be read off the page rather than worked out
  from a sentence.

  The saving is drawn as well as stated, in the same quiet navy the stage rail
  uses, and the rows sitting at the floor are the ones that get solid ink.
*/

type Batch = NonNullable<NonNullable<Quote['batch']>>;
type Tier = Batch['prices'][number];

const head =
  'py-2.5 font-sans text-xs font-medium text-slate';

export function BatchTable({ batch }: { batch: Batch }) {
  const tiers: Tier[] = [...batch.prices].sort((a, b) => a.quantity - b.quantity);
  if (tiers.length === 0) return null;

  const units = tiers.map((t) => t.unit);
  const cheapest = Math.min(...units);
  const base = tiers[0].unit;
  const bestSaving = base - cheapest;
  const floorAt = tiers.find((t) => t.unit === cheapest);
  const oneOff = tiers[0].quantity === 1;

  return (
    <section>
      <h3 className="font-display text-[0.9375rem] font-semibold tracking-tight text-navy">Batch pricing</h3>
      <p className="mt-2 max-w-[58ch] font-sans text-sm leading-relaxed text-slate">
        {batch.max_per_plate} fit on one plate.{' '}
        {bestSaving > 0 && floorAt ? (
          <>
            Unit price bottoms out at {money(cheapest)} from {floorAt.quantity} up, a{' '}
            {Math.round((bestSaving / base) * 100)}% cut on printing{' '}
            {oneOff ? 'one' : `${tiers[0].quantity}`}.
          </>
        ) : (
          <>Unit price holds at {money(cheapest)} across every quantity dapi priced.</>
        )}
      </p>

      <div className="mt-5 overflow-x-auto">
        <table className="w-full min-w-[24rem] border-collapse text-left">
          <thead>
            <tr className="border-y border-mist">
              <th scope="col" className={cn(head, 'pr-4')}>
                Quantity
              </th>
              <th scope="col" className={cn(head, 'px-4 text-right')}>
                Plates
              </th>
              <th scope="col" className={cn(head, 'px-4 text-right')}>
                Unit
              </th>
              <th scope="col" className={cn(head, 'px-4 text-right')}>
                Total
              </th>
              <th scope="col" className={cn(head, 'w-[6rem] pl-4 text-right sm:w-[11rem]')}>
                Saved
              </th>
            </tr>
          </thead>
          <tbody className="font-sans text-sm">
            {tiers.map((tier) => {
              const atFloor = bestSaving > 0 && tier.unit === cheapest;
              const saving = base - tier.unit;
              const percent = Math.round((saving / base) * 100);
              const width = bestSaving > 0 ? (saving / bestSaving) * 100 : 0;
              return (
                <tr
                  key={tier.quantity}
                  className={cn('border-b border-mist', atFloor && 'bg-mist/40')}
                >
                  <th
                    scope="row"
                    className={cn(
                      'py-3 pr-4 text-left font-normal tabular-nums',
                      atFloor ? 'text-navy' : 'text-slate',
                    )}
                  >
                    {tier.quantity}
                    <span className="text-slate"> ×</span>
                  </th>
                  <td className="px-4 py-3 text-right tabular-nums text-slate">{tier.plates}</td>
                  <td
                    className={cn(
                      'px-4 py-3 text-right tabular-nums text-navy',
                      atFloor && 'font-medium',
                    )}
                  >
                    {money(tier.unit)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-navy">
                    {money(tier.total)}
                  </td>
                  <td className="py-3 pl-4">
                    <span className="flex items-center justify-end gap-3">
                      {/* Drawn as well as stated: the bar spans the best saving on offer. */}
                      <span
                        aria-hidden="true"
                        className="hidden h-1.5 flex-1 overflow-hidden rounded-full bg-mist sm:block"
                      >
                        <span
                          className={cn('block h-full rounded-full', atFloor ? 'bg-navy' : 'bg-navy/40')}
                          style={{ width: `${width}%` }}
                        />
                      </span>
                      <span
                        className={cn(
                          'w-11 shrink-0 text-right tabular-nums',
                          percent > 0 ? 'text-navy' : 'text-slate/50',
                        )}
                      >
                        {percent > 0 ? `${percent}%` : 'none'}
                      </span>
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-3 font-sans text-xs leading-relaxed text-slate/80">
        Saved is the cut in unit price against printing {oneOff ? 'one' : `${tiers[0].quantity}`}.
        <span className="hidden sm:inline"> The bar spans the deepest cut on offer.</span>
      </p>
    </section>
  );
}
