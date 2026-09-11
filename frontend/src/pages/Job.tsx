import type { ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft } from 'lucide-react';
import { getJob, getJobEvents, isLive, type Job as JobRecord, type JobEvent } from '@/api/jobs';
import { BatchTable } from '@/components/site/BatchTable';
import { StageRail } from '@/components/site/JobRow';
import { ConsoleNav, SiteHeader } from '@/components/site/SiteHeader';
import { Spinner } from '@/components/site/Spinner';
import { formatMinutes, formatWhen } from '@/lib/format';
import { STATUS_LABEL } from '@/lib/stages';
import { cn } from '@/lib/utils';

/*
  One job: what dapi's slicer made of one model file.

  The page answers three questions in the order an operator asks them. What
  does one cost, what is it made of, and what happens if I print a lot of them.
  Everything below the fold is the record of how it got here.
*/

const eyebrow = 'font-sans text-[0.6875rem] uppercase tracking-[0.22em] text-slate/60';
const apiUrl = import.meta.env.VITE_API_URL ?? '/api';
const h2 = 'font-display text-[0.6875rem] font-semibold uppercase tracking-[0.22em] text-slate/60';
const money = (n: number) => `$${n.toFixed(2)}`;

/* What the pipeline saw: the drafts research judged from, and the finals once a price exists. */
function Renders({ job }: { job: JobRecord }) {
  const r = job.renders;
  if (!r || (r.drafts.length === 0 && !r.hero)) return null;
  return (
    <section className="mt-14">
      <h2 className={h2}>Renders</h2>
      {r.hero ? (
        <img src={r.hero} alt={`${job.fileName}, hero render`} className="mt-5 w-full max-w-[520px] rounded-2xl border border-mist bg-white" />
      ) : null}
      {r.drafts.length > 0 ? (
        <div className="mt-4 flex gap-2 overflow-x-auto">
          {r.drafts.map((src) => (
            <img key={src} src={src} alt="" className="size-24 shrink-0 rounded-lg border border-mist bg-white" />
          ))}
        </div>
      ) : null}
      <p className="mt-2 font-sans text-xs text-slate/80">
        {r.hero ? 'Hero at 1024 px and the eight drafts it was chosen from.' : 'Eight drafts at 320 px. Finals render once a sale price exists.'}
      </p>
    </section>
  );
}

/* The market band research found, and the listings it was built from. */
function Market({ job }: { job: JobRecord }) {
  const v = job.viability;
  const c = job.comparables;
  if (!v) return null;
  if (v.verdict === 'no_market' || !v.market) {
    return (
      <section className="mt-14">
        <h2 className={h2}>Market</h2>
        <p className="mt-3 max-w-[56ch] font-sans text-sm leading-relaxed text-slate">
          Research found no priced comparables{c?.queries?.length ? ` for “${c.queries.join('”, “')}”` : ''}. {v.reason ?? ''}
        </p>
      </section>
    );
  }
  const m = v.market;
  return (
    <section className="mt-14">
      <h2 className={h2}>Market</h2>
      <p className="mt-3 max-w-[60ch] font-sans text-sm leading-relaxed text-slate">
        {c?.product ? <span className="text-navy">{c.product}. </span> : null}
        {m.n} comparable{m.n === 1 ? '' : 's'} on Etsy{c?.queries?.length ? ` for “${c.queries.join('”, “')}”` : ''},
        {' '}from {money(m.min)} to {money(m.max)}.
      </p>
      <dl className="mt-6 grid grid-cols-2 gap-x-8 gap-y-7 sm:grid-cols-4">
        <Stat label="market median" value={money(m.median)} note={`p25 ${money(m.p25)} · p75 ${money(m.p75)}`} />
        {v.breakEven != null ? <Stat label="break-even" value={money(v.breakEven)} note="dapi cost, fees and VAT owed" /> : null}
        {v.markupPrice != null ? <Stat label="cost plus markup" value={money(v.markupPrice)} note="the policy markup on the quote" /> : null}
        <Stat
          label="verdict"
          value={<span className={v.verdict === 'not_viable' ? 'text-destructive' : undefined}>{v.verdict.replace('_', ' ')}</span>}
          note={v.atMedian?.margin != null ? `${money(v.atMedian.margin)} margin at the median` : undefined}
        />
      </dl>
      {c?.top?.length ? (
        <ul className="mt-6 border-t border-mist font-sans text-sm">
          {c.top.map((x) => (
            <li key={x.url} className="flex items-baseline gap-3 border-b border-mist py-2.5">
              <a href={x.url} target="_blank" rel="noreferrer" className="min-w-0 flex-1 truncate text-navy underline-offset-4 hover:underline">
                {x.title}
              </a>
              <span className="tabular-nums text-navy">{money(x.price)}</span>
              <span className="w-24 text-right tabular-nums text-slate/70">{x.sold != null ? `${x.sold} sold` : ''}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

/* The one decision a person confirms at publish: the proposed sale price and the case for it. */
function SalePrice({ job }: { job: JobRecord }) {
  const p = job.pricing;
  if (!p) return null;
  const prop = p.proposal;
  return (
    <section className="mt-14">
      <h2 className={h2}>Sale price</h2>
      {prop ? (
        <div className="mt-5 flex flex-col gap-6 sm:flex-row sm:items-start sm:gap-12">
          <div className="shrink-0">
            <div className="font-display text-[2.75rem] font-semibold leading-none tabular-nums tracking-tight text-navy">
              {money(prop.price)}
            </div>
            <div className={cn(eyebrow, 'mt-2')}>{prop.position} · {prop.candidate} candidate</div>
          </div>
          <div className="min-w-0">
            <p className="max-w-[58ch] font-sans text-sm leading-relaxed text-navy">{prop.rationale}</p>
            {prop.runner_up ? (
              <p className="mt-2 max-w-[58ch] font-sans text-xs leading-relaxed text-slate">
                Runner-up {prop.runner_up.candidate} at {money(prop.runner_up.price)}: {prop.runner_up.why_not}
              </p>
            ) : null}
            {prop.override ? (
              <p className="mt-2 font-sans text-xs text-slate">Overridden by {prop.override.by}: {prop.override.reason}</p>
            ) : null}
          </div>
        </div>
      ) : (
        <p className="mt-3 max-w-[58ch] font-sans text-sm leading-relaxed text-destructive">
          {p.rejected ?? 'No price chosen yet.'}
        </p>
      )}
      <dl className="mt-6 grid grid-cols-2 gap-x-8 gap-y-7 sm:grid-cols-4">
        {p.floor != null ? <Stat label="floor" value={money(p.floor)} note="break-even, the hard rule for any author" /> : null}
        {p.minMarginPct != null ? <Stat label="min margin" value={`${Math.round(p.minMarginPct * 100)}%`} note="the model proposes nothing under it" /> : null}
      </dl>
      {p.candidates.length + p.rejectedCandidates.length > 0 ? (
        <ul className="mt-6 border-t border-mist font-sans text-sm">
          {[...p.candidates, ...p.rejectedCandidates].map((c) => (
            <li key={c.label} className="grid grid-cols-[6rem_5rem_1fr] items-baseline gap-x-4 border-b border-mist py-2.5 sm:grid-cols-[6rem_5rem_8rem_1fr]">
              <span className="text-navy">{c.label}</span>
              <span className="tabular-nums text-navy">{money(c.price)}</span>
              <span className="tabular-nums text-slate">
                {c.margin_pct != null ? `${Math.round(c.margin_pct * 100)}% margin` : ''}
              </span>
              <span className={cn('col-span-3 sm:col-span-1', c.rejected_because ? 'text-destructive' : 'text-slate')}>
                {c.rejected_because
                  ? c.rejected_because === 'below_floor'
                    ? `rejected: ${money(c.below_floor_by ?? 0)} below the floor`
                    : 'rejected: under the minimum margin'
                  : c.position_in_band.replace(/_/g, ' ')}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function Stat({ label, value, note }: { label: string; value: ReactNode; note?: ReactNode }) {
  return (
    <div>
      <dt className={eyebrow}>{label}</dt>
      <dd className="mt-1.5 font-display text-xl font-semibold tabular-nums tracking-tight text-navy">
        {value}
      </dd>
      {note ? <p className="mt-1 font-sans text-xs leading-relaxed text-slate">{note}</p> : null}
    </div>
  );
}

/* The one number the page exists for, or the reason there isn't one yet. */
function PriceBlock({ job }: { job: JobRecord }) {
  if (job.price != null) {
    return (
      <div className="shrink-0 sm:text-right">
        <div className="font-display text-[2.75rem] font-semibold leading-none tabular-nums tracking-tight text-navy">
          ${job.price.toFixed(2)}
        </div>
        <div className={cn(eyebrow, 'mt-2')}>dapi, per unit</div>
      </div>
    );
  }
  if (job.status === 'failed' || job.status === 'blocked' || !isLive(job)) {
    return (
      <div className="shrink-0 sm:text-right">
        <div className="font-display text-2xl font-semibold leading-none tracking-tight text-destructive">
          No price
        </div>
        <div className={cn(eyebrow, 'mt-2')}>{job.status === 'failed' ? 'the slicer stopped' : 'not sliced yet'}</div>
      </div>
    );
  }
  return (
    <div className="flex shrink-0 items-center gap-3">
      <Spinner className="size-4" />
      <div>
        <div className="font-sans text-[0.9375rem] text-navy">Not priced yet</div>
        <div className={cn(eyebrow, 'mt-1')}>this page updates itself</div>
      </div>
    </div>
  );
}

function Metrics({ job }: { job: JobRecord }) {
  const m = job.metrics;
  if (!m) {
    if (job.status !== 'done') return null;
    return (
      <p className="mt-8 max-w-[58ch] font-sans text-sm leading-relaxed text-slate">
        This quote carries no slicer measurements. dapi started recording them on 8 September;
        quotes older than that were priced without them.
      </p>
    );
  }
  return (
    <dl className="mt-8 grid grid-cols-2 gap-x-8 gap-y-7 sm:grid-cols-4">
      <Stat
        label="in the part"
        value={`${m.model_g.toFixed(1)} g`}
        note="the shipping weight"
      />
      <Stat
        label="extruded"
        value={`${m.total_g.toFixed(1)} g`}
        note="supports and purge included"
      />
      <Stat
        label="print time"
        value={formatMinutes(m.print_time_s)}
        note={m.prepare_time_s ? `plus ${formatMinutes(m.prepare_time_s)} preparing the plate` : undefined}
      />
      {m.filament_count == null ? null : (
        <Stat label="filaments" value={m.filament_count} note="on the busiest plate" />
      )}
    </dl>
  );
}

function Events({ events }: { events: JobEvent[] }) {
  return (
    <section className="mt-14">
      <h2 className="font-display text-[0.6875rem] font-semibold uppercase tracking-[0.22em] text-slate/60">
        Record
      </h2>
      <ol className="mt-5 border-t border-mist font-sans text-sm">
        {events.map((e, i) => (
          <li
            key={`${e.stage}-${i}`}
            className="grid grid-cols-[5.5rem_1fr] items-baseline gap-x-4 gap-y-1 border-b border-mist py-3 sm:grid-cols-[5.5rem_6rem_1fr_auto]"
          >
            <span className="text-navy">{e.stage}</span>
            <span className={cn(e.status === 'failed' ? 'text-destructive' : 'text-slate')}>
              {STATUS_LABEL[e.status]}
            </span>
            <span className="col-start-2 tabular-nums text-slate sm:col-start-3">
              {e.durationMs > 0 ? `${(e.durationMs / 1000).toFixed(1)} s` : ''}
            </span>
            <span className="col-start-2 tabular-nums text-slate sm:col-start-4 sm:text-right">
              {formatWhen(e.at)}
            </span>
          </li>
        ))}
        {events.length === 0 ? (
          <li className="py-3 text-slate">Nothing has run yet.</li>
        ) : null}
      </ol>
    </section>
  );
}

export default function Job() {
  const { jobId = '' } = useParams();
  const job = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => getJob(jobId),
    refetchInterval: (q) => (isLive(q.state.data) ? 3000 : false),
  });
  const events = useQuery({ queryKey: ['job', jobId, 'events'], queryFn: () => getJobEvents(jobId) });

  return (
    <div className="min-h-svh bg-paper text-navy">
      <SiteHeader>
        <ConsoleNav />
      </SiteHeader>
      <main className="mx-auto max-w-[940px] px-5 pb-24 sm:px-10">
        <Link
          to="/agent-runs"
          className="inline-flex items-center gap-1.5 font-sans text-sm text-slate underline-offset-4 transition-colors hover:text-navy hover:underline"
        >
          <ArrowLeft className="size-3.5" aria-hidden="true" />
          Agent runs
        </Link>

        {job.isPending ? (
          <p className="flex items-center gap-2 py-10 font-sans text-sm text-slate">
            <Spinner className="size-4" />
            Loading this job.
          </p>
        ) : job.isError ? (
          <div className="py-10">
            <h1 className="font-display text-2xl font-semibold tracking-tight">
              This job did not load
            </h1>
            <p className="mt-2 max-w-[52ch] font-sans text-sm leading-relaxed text-slate">
              {job.error instanceof Error ? job.error.message : 'The request failed.'} The quote
              was asked for at {apiUrl}. Either the id is wrong or the API is unreachable.
            </p>
            <button
              type="button"
              onClick={() => job.refetch()}
              className="mt-4 font-sans text-sm text-navy underline underline-offset-4"
            >
              Try again
            </button>
          </div>
        ) : (
          <>
            <header className="mt-7 flex flex-col gap-6 border-b border-mist pb-8 sm:flex-row sm:items-end sm:justify-between sm:gap-12">
              <div className="min-w-0">
                <div className="flex items-center gap-3">
                  <StageRail stage={job.data.stage} status={job.data.status} />
                  <span
                    className={cn(
                      'font-sans text-[0.6875rem] uppercase tracking-[0.22em]',
                      job.data.status === 'failed' ? 'text-destructive' : 'text-slate/60',
                    )}
                  >
                    {job.data.stage} · {STATUS_LABEL[job.data.status]}
                  </span>
                </div>
                <h1 className="mt-4 font-display text-[2.5rem] font-semibold leading-[1.05] tracking-tight [overflow-wrap:anywhere]">
                  {job.data.fileName}
                </h1>
                <p className="mt-2 font-sans text-xs tabular-nums text-slate">
                  {job.data.quoteId ? `quote ${job.data.quoteId}` : 'not quoted yet'} · started{' '}
                  {formatWhen(job.data.createdAt)}
                  {job.data.stripped ? ' · foreign project settings stripped before slicing' : ''}
                </p>
                {job.data.reason ? (
                  <p
                    className={cn(
                      'mt-3 font-sans text-sm',
                      job.data.status === 'failed' ? 'text-destructive' : 'text-slate',
                    )}
                  >
                    {job.data.reason}
                  </p>
                ) : null}
              </div>
              <PriceBlock job={job.data} />
            </header>

            <Metrics job={job.data} />

            {job.data.batch?.prices?.length ? (
              <BatchTable batch={job.data.batch} />
            ) : job.data.status === 'done' ? (
              <section className="mt-14">
                <h2 className="font-display text-[0.6875rem] font-semibold uppercase tracking-[0.22em] text-slate/60">
                  Batch pricing
                </h2>
                <p className="mt-3 max-w-[56ch] font-sans text-sm leading-relaxed text-slate">
                  dapi priced a single unit for this file. Batch tiers arrive when the slicer can
                  fit more than one on a plate.
                </p>
              </section>
            ) : null}

            <Market job={job.data} />
            <SalePrice job={job.data} />
            <Renders job={job.data} />

            <Events events={events.data ?? []} />
          </>
        )}
      </main>
    </div>
  );
}
