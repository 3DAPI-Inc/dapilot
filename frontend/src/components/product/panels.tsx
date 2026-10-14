import type { ReactNode } from 'react';
import { ExternalLink } from 'lucide-react';
import type { Job, JobEvent, MarketBand } from '@/api/jobs';
import { BatchTable } from '@/components/site/BatchTable';
import { Notice } from '@/components/site/ConsoleShell';
import { IdentityLine } from '@/components/site/Identity';
import { Zoomable } from '@/components/site/Lightbox';
import { pct, recommendedPrice } from '@/components/site/PriceDecision';
import { formatMinutes, formatWhen, money } from '@/lib/format';
import { STATUS_LABEL } from '@/lib/stages';
import { cn } from '@/lib/utils';

/*
  The detail behind the product page, one tab each. The page above answers
  "what is it and what do I do"; these answer "why", and only when asked.
*/

const h3 = 'font-display text-[0.9375rem] font-semibold tracking-tight text-navy';
const th = 'py-2.5 font-sans text-xs font-medium text-slate';
/** Script reasons arrive lower-case and unpunctuated; on the page they are sentences. */
const sentence = (s: string) => `${s.charAt(0).toUpperCase()}${s.slice(1).replace(/[.\s]+$/, '')}.`;

function Figure({ label, value, note }: { label: string; value: ReactNode; note?: ReactNode }) {
  return (
    <div>
      <dt className="font-sans text-xs text-slate">{label}</dt>
      <dd className="mt-1 font-display text-xl font-semibold tabular-nums tracking-tight text-navy">{value}</dd>
      {note ? <p className="mt-0.5 font-sans text-xs leading-relaxed text-slate">{note}</p> : null}
    </div>
  );
}

/*
  The market as one picture: where Etsy's sales sit, where we stop making
  money, and where our price lands. A long tail past the p75 is cut off and
  named at the right edge instead of squashing everything else to the left.
*/
function PriceBand({ band, floor, ours }: { band: MarketBand; floor: number | null; ours: number | null }) {
  const reach = Math.max(band.p75 * 1.5, band.median, floor ?? 0, ours ?? 0);
  const hi = band.max <= reach * 1.15 ? Math.max(band.max, reach / 1.5) : reach;
  const lo = Math.min(band.min, floor ?? band.min, ours ?? band.min, hi * 0.98);
  const x = (n: number) => `${Math.min(100, Math.max(0, ((n - lo) / (hi - lo || 1)) * 100))}%`;
  const cut = band.max > hi;
  return (
    <figure aria-label={`Market from ${money(band.min)} to ${money(band.max)}, median ${money(band.median)}`}>
      <div className="relative h-16">
        {ours != null ? (
          <span className="absolute top-0 -translate-x-1/2 font-sans text-xs font-medium whitespace-nowrap text-navy" style={{ left: x(ours) }}>
            Ours {money(ours)}
          </span>
        ) : null}
        <div className="absolute inset-x-0 top-8 h-3 rounded-full bg-mist" />
        <div className="absolute top-8 h-3 rounded-full bg-ice/60" style={{ left: x(band.p25), width: `calc(${x(band.p75)} - ${x(band.p25)})` }} />
        <span className="absolute top-[1.625rem] h-6 w-0.5 -translate-x-1/2 rounded-full bg-navy/70" style={{ left: x(band.median) }} />
        {floor != null ? (
          <span className="absolute top-[1.625rem] h-6 w-0 -translate-x-1/2 border-l-2 border-dashed border-destructive" style={{ left: x(floor) }} />
        ) : null}
        {ours != null ? (
          <span className="absolute top-[1.8125rem] size-[1.125rem] -translate-x-1/2 rounded-full border-[3px] border-white bg-navy shadow-hairline" style={{ left: x(ours) }} />
        ) : null}
      </div>
      <div className="flex justify-between font-sans text-xs tabular-nums text-slate">
        <span>{money(lo)}</span>
        <span>{cut ? `${money(hi)} · tail runs to ${money(band.max)}` : money(hi)}</span>
      </div>
      <figcaption className="mt-4 flex flex-wrap gap-x-5 gap-y-2 font-sans text-xs text-slate">
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-5 rounded-full bg-ice/60" />Middle half of sales, {money(band.p25)} to {money(band.p75)}</span>
        <span className="flex items-center gap-1.5"><span className="h-3.5 w-0.5 rounded-full bg-navy/70" />Median {money(band.median)}</span>
        {floor != null ? <span className="flex items-center gap-1.5"><span className="h-3.5 border-l-2 border-dashed border-destructive" />Break-even {money(floor)}</span> : null}
      </figcaption>
    </figure>
  );
}

export function MarketPanel({ job }: { job: Job }) {
  const v = job.viability;
  const c = job.comparables;
  if (!v) return <Notice title="No market research yet">It runs after the first drafts, and before anything that costs money.</Notice>;
  const queries = c?.queries?.length ? `“${c.queries.join('”, “')}”` : null;
  if (v.verdict === 'no_market' || !v.market) {
    return <Notice title="No priced comparables found">{queries ? `Searched Etsy for ${queries}. ` : ''}{v.reason ?? ''}</Notice>;
  }
  const m = v.market;
  const identity = v.identity ?? c?.identity ?? null;
  const ours = job.pricing?.approved?.price ?? job.pricing?.proposal?.price ?? recommendedPrice(job.pricing);
  const floor = job.pricing?.floor ?? v.breakEven ?? null;
  const top = m.weight_concentration;
  const notes = [
    m.basis === 'weighted' ? 'Weighted by estimated units sold a day, so the median is where half the sales happen.' : 'Unweighted: what sellers ask, not what buyers pay.',
    m.thin ? 'Thin market: too few or too scattered listings for the median to mean much.' : null,
    top && top.top_shop_share >= 0.5 ? `${top.top_shop ?? 'One shop'} holds ${Math.round(top.top_shop_share * 100)}% of the sales.` : null,
  ].filter(Boolean);

  return (
    <div className="grid gap-10 p-6 sm:p-8">
      <div>
        <p className="font-sans text-sm text-slate">
          <span className="font-medium text-navy">{m.n} comparable{m.n === 1 ? '' : 's'} on Etsy</span>
          {queries ? ` for ${queries}` : ''}
          {v.atMedian?.margin != null ? `. At the median we would keep ${money(v.atMedian.margin)} a sale.` : '.'}
        </p>
        {identity ? <IdentityLine id={identity} fileName={job.fileName} className="mt-2" /> : null}
        <div className="mt-6 max-w-[44rem]">
          <PriceBand band={m} floor={floor} ours={ours} />
        </div>
        <ul className="mt-5 grid gap-1 font-sans text-xs leading-relaxed text-slate">
          {notes.map((n) => (
            <li key={n} className={cn(n?.startsWith('Thin') && 'text-warn')}>{n}</li>
          ))}
        </ul>
      </div>

      {c?.top?.length ? (
        <div>
          <h3 className={h3}>Closest listings</h3>
          <ul className="mt-3 divide-y divide-mist border-y border-mist">
            {c.top.map((x) => (
              <li key={x.url} className="flex items-center gap-4 py-3">
                {x.image ? (
                  <Zoomable media={{ kind: 'image', src: x.image, label: x.title }} className="size-12 shrink-0 rounded-lg border border-mist bg-white" />
                ) : null}
                <div className="min-w-0 flex-1">
                  <a href={x.url} target="_blank" rel="noreferrer" className="group flex items-center gap-1.5 font-sans text-sm text-navy underline-offset-4 hover:underline">
                    <span className="truncate">{x.title}</span>
                    <ExternalLink className="size-3 shrink-0 text-slate/50 group-hover:text-navy" aria-hidden="true" />
                  </a>
                  <p className="mt-0.5 truncate font-sans text-xs text-slate">
                    {[x.shop, x.unitsPerDay != null ? `about ${x.unitsPerDay.toFixed(2)} sold a day` : x.sold != null ? `${x.sold.toLocaleString()} shop sales` : null].filter(Boolean).join(' · ')}
                  </p>
                </div>
                <span className="shrink-0 font-sans text-sm font-medium tabular-nums text-navy">{money(x.price)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export function PricingPanel({ job }: { job: Job }) {
  const p = job.pricing;
  if (!p) return <Notice title="No price candidates yet">They are worked out once the market has been read.</Notice>;
  const said = p.proposal?.proposed ?? p.proposal;
  const rows = [...p.candidates, ...p.rejectedCandidates];
  const chosen = p.approved?.price ?? p.proposal?.price ?? null;
  const sellers = p.p75Sellers;
  const shops = sellers?.distinct_named_shops ?? sellers?.shops?.length ?? null;
  const topShare = sellers?.top_share ?? sellers?.shops?.[0]?.weight_share ?? null;

  return (
    <div className="grid gap-10 p-6 sm:p-8">
      <div className="max-w-[64ch]">
        <h3 className={h3}>{said ? `Why ${money(said.price)}` : 'No recommendation'}</h3>
        {said ? (
          <>
            <p className="mt-2 font-sans text-sm leading-relaxed text-slate">{said.rationale}</p>
            {said.runner_up ? (
              <p className="mt-2 font-sans text-sm leading-relaxed text-slate">
                <span className="text-navy">Runner-up, {said.runner_up.candidate} at {money(said.runner_up.price)}:</span> {said.runner_up.why_not}
              </p>
            ) : null}
          </>
        ) : (
          <p className="mt-2 font-sans text-sm leading-relaxed text-destructive">{sentence(p.rejected ?? 'No price chosen yet')} You can still set one at or above the floor.</p>
        )}
        {p.proposal?.override ? (
          <p className="mt-2 font-sans text-sm leading-relaxed text-slate">
            <span className="text-navy">Overridden to {money(p.proposal.price)} by {p.proposal.override.by}:</span> {p.proposal.override.reason}
          </p>
        ) : null}
      </div>

      {rows.length > 0 || p.skipped.length > 0 ? (
        <div>
          <h3 className={h3}>Candidates</h3>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[32rem] border-collapse text-left font-sans text-sm">
              <thead>
                <tr className="border-y border-mist">
                  <th scope="col" className={th}>Candidate</th>
                  <th scope="col" className={cn(th, 'text-right')}>Price</th>
                  <th scope="col" className={cn(th, 'text-right')}>Fees</th>
                  <th scope="col" className={cn(th, 'text-right')}>You keep</th>
                  <th scope="col" className={cn(th, 'text-right')}>Margin</th>
                  <th scope="col" className={cn(th, 'pl-6')}>Standing</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => {
                  const dead = Boolean(c.rejected_because);
                  const picked = chosen != null && Math.abs(c.price - chosen) < 0.005;
                  return (
                    <tr key={c.label} className={cn('border-b border-mist', picked && 'bg-mist/40')}>
                      <th scope="row" className="py-3 pr-4 font-normal">
                        <span className="capitalize text-navy">{c.label}</span>
                        {c.how ? <span className="block text-xs text-slate">{c.how}</span> : null}
                      </th>
                      <td className={cn('py-3 text-right tabular-nums', dead ? 'text-slate' : 'font-medium text-navy')}>{money(c.price)}</td>
                      <td className="py-3 text-right tabular-nums text-slate">{money(c.fees)}</td>
                      <td className={cn('py-3 text-right tabular-nums', c.margin < 0 ? 'text-destructive' : 'text-navy')}>{money(c.margin)}</td>
                      <td className="py-3 text-right tabular-nums text-slate">{pct(c.margin_pct)}</td>
                      <td className={cn('py-3 pl-6', dead ? 'text-destructive' : 'text-slate')}>
                        {dead
                          ? c.rejected_because === 'below_floor'
                            ? `${money(c.below_floor_by ?? 0)} below the floor`
                            : 'Under the minimum margin'
                          : picked
                            ? 'Chosen'
                            : (c.position_in_band ?? 'Allowed').replace(/_/g, ' ').replace(/^./, (ch) => ch.toUpperCase())}
                      </td>
                    </tr>
                  );
                })}
                {p.skipped.map((s) => (
                  <tr key={s.label} className="border-b border-mist text-slate">
                    <th scope="row" className="py-3 pr-4 font-normal capitalize">{s.label}</th>
                    <td colSpan={5} className="py-3">Not offered: {s.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 font-sans text-xs leading-relaxed text-slate">
            {p.floor != null ? `The floor is ${money(p.floor)}: cost, platform fees and VAT owed. ` : ''}
            {shops != null && topShare != null
              ? `At or above the p75 there ${shops === 1 ? 'is 1 named shop' : `are ${shops} named shops`}, the largest with ${Math.round(topShare * 100)}% of the sales.`
              : ''}
          </p>
        </div>
      ) : null}
    </div>
  );
}

export function ProductionPanel({ job }: { job: Job }) {
  const m = job.metrics;
  const a = job.analysis;
  const stock = job.viability?.stock;
  if (!m && !a && job.price == null) return <Notice title="Not sliced yet">Weight, print time and batch pricing come from dapi's slicer.</Notice>;
  return (
    <div className="grid gap-10 p-6 sm:p-8">
      <dl className="grid grid-cols-2 gap-x-8 gap-y-6 sm:grid-cols-4">
        {m ? (
          <>
            <Figure label="In the part" value={`${m.model_g.toFixed(1)} g`} note="The shipping weight" />
            <Figure label="Extruded" value={`${m.total_g.toFixed(1)} g`} note="Supports and purge included" />
            <Figure label="Print time" value={formatMinutes(m.print_time_s)} note={m.prepare_time_s ? `Plus ${formatMinutes(m.prepare_time_s)} preparing the plate` : undefined} />
            {m.filament_count != null ? <Figure label="Filaments" value={m.filament_count} note="On the busiest plate" /> : null}
          </>
        ) : null}
        {a ? (
          <>
            <Figure label="Size" value={<span className="text-base">{a.bbox_mm.map((n) => n.toFixed(1)).join(' × ')} mm</span>} />
            {/* Open bodies report no volume; the slicer's grams are the authority then. */}
            {a.volume_mm3 >= 50 ? <Figure label="Volume" value={`${(a.volume_mm3 / 1000).toFixed(1)} cm³`} /> : null}
            <Figure label="Bodies" value={a.bodies} note={a.watertight_bodies === a.bodies ? 'All closed' : `${a.watertight_bodies} closed`} />
            <Figure label="Triangles" value={a.triangles.toLocaleString()} />
          </>
        ) : null}
      </dl>
      {!m && job.status === 'done' ? (
        <p className="max-w-[58ch] font-sans text-sm leading-relaxed text-slate">
          This quote carries no slicer measurements. dapi started recording them on 8 September; older quotes were priced without them.
        </p>
      ) : null}

      {job.batch?.prices?.length ? <BatchTable batch={job.batch} /> : null}

      {stock ? (
        <div>
          <h3 className={h3}>A stock run of {stock.units}</h3>
          <p className="mt-2 font-sans text-sm text-slate">
            {stock.plates} plate{stock.plates === 1 ? '' : 's'}, {money(stock.batch_cost)} in total, {money(stock.unit_cost)} each.
          </p>
          <ul className="mt-3 grid gap-x-8 gap-y-1 font-sans text-sm text-slate sm:grid-cols-3">
            {stock.payback.map((b) => (
              <li key={b.label}>
                <span className="text-navy">At {money(b.price)}</span> ({b.label}):{' '}
                {b.units_to_pay_back != null ? `paid back after ${b.units_to_pay_back} sales` : 'never paid back'}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export function ActivityPanel({ job, events }: { job: Job; events: JobEvent[] }) {
  const d = job.design;
  return (
    <div className="grid gap-8 p-6 sm:p-8">
      <p className="font-sans text-sm text-slate">
        Started {formatWhen(job.createdAt)}
        {job.quoteId ? <> · quote <span className="font-mono text-[0.8125rem] text-navy">{job.quoteId}</span></> : null}
        {job.stripped ? ' · foreign project settings were stripped before slicing' : ''}
      </p>

      {d ? (
        <div>
          <h3 className={h3}>Photos and video</h3>
          <p className="mt-2 max-w-[64ch] font-sans text-sm leading-relaxed text-slate">{d.reason}</p>
          <p className="mt-2 font-sans text-xs text-slate">
            {d.photos.length} photos in listing order · about {money(d.spendUsd)} on generation
            {d.rejected.length > 0 ? ` · rejected: ${d.rejected.map((r) => r.id).join(', ')}` : ''}
            {d.failed.length > 0 ? ` · failed on fal: ${d.failed.map((r) => r.id).join(', ')}` : ''}
          </p>
          {d.warnings.map((w) => (
            <p key={w} className="mt-1 font-sans text-xs text-destructive">{w}</p>
          ))}
        </div>
      ) : null}

      <ol className="divide-y divide-mist border-y border-mist font-sans text-sm">
        {events.map((e, i) => (
          <li key={`${e.stage}-${i}`} className="grid grid-cols-[6rem_1fr_auto] items-baseline gap-x-4 py-2.5 sm:grid-cols-[7rem_7rem_1fr_auto]">
            <span className="capitalize text-navy">{e.stage}</span>
            <span className={e.status === 'failed' ? 'text-destructive' : 'text-slate'}>{STATUS_LABEL[e.status]}</span>
            <span className="hidden tabular-nums text-slate sm:block">{e.durationMs > 0 ? `${(e.durationMs / 1000).toFixed(1)} s` : ''}</span>
            <span className="text-right tabular-nums text-slate">{formatWhen(e.at)}</span>
          </li>
        ))}
        {events.length === 0 ? <li className="py-3 text-slate">Nothing has run yet.</li> : null}
      </ol>

      {job.log ? (
        <details className="group">
          <summary className="cursor-pointer font-sans text-sm text-navy underline-offset-4 hover:underline">Worker log</summary>
          <pre className="mt-3 max-h-96 overflow-auto rounded-xl bg-mist/50 p-4 font-mono text-xs leading-relaxed whitespace-pre-wrap text-slate">{job.log}</pre>
        </details>
      ) : null}
    </div>
  );
}
