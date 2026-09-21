import { useState, type ReactNode } from 'react';
import { Check, TriangleAlert } from 'lucide-react';
import { isLive, type Job, type Viability } from '@/api/jobs';
import { StatusText } from '@/components/site/JobRow';
import { PriceDialog, errorText, pct, recommendedPrice, useDecidePrice } from '@/components/site/PriceDecision';
import { Spinner } from '@/components/site/Spinner';
import { Button } from '@/components/ui/button';
import { formatMinutes, money } from '@/lib/format';
import { cn } from '@/lib/utils';

/*
  Where a shop page has its buy box, this page has the decision. The name, the
  one price that matters right now, the one thing a person can do about it,
  and a handful of facts. Everything that explains those lives in the tabs.
*/

const VERDICT: Record<Viability['verdict'], { label: string; tone: string }> = {
  viable: { label: 'Viable', tone: 'text-ok' },
  thin: { label: 'Thin market', tone: 'text-warn' },
  not_viable: { label: 'Not viable', tone: 'text-destructive' },
  no_market: { label: 'No market found', tone: 'text-slate' },
  unreliable: { label: 'Band not evidence', tone: 'text-destructive' },
};

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-6 border-t border-mist py-2.5 first:border-t-0">
      <dt className="shrink-0 font-sans text-sm text-slate">{label}</dt>
      <dd className="min-w-0 text-right font-sans text-sm tabular-nums text-navy">{children}</dd>
    </div>
  );
}

export function BuyBox({ job }: { job: Job }) {
  const p = job.pricing;
  const v = job.viability;
  const approved = p?.approved ?? null;
  const recommended = recommendedPrice(p);
  const headline = approved?.price ?? recommended;
  const [dialog, setDialog] = useState(false);
  const approve = useDecidePrice(job);

  const match = headline != null ? p?.candidates.find((c) => Math.abs(c.price - headline) < 0.005) : undefined;
  const margin = approved ? { usd: approved.margin, pct: approved.margin_pct } : match ? { usd: match.margin, pct: match.margin_pct } : null;
  const unitCost = approved?.unit_cost ?? v?.unitCost ?? null;
  // The same name the catalogue files it under: the designer's, else what research called it, else the file.
  const noun = job.comparables?.product ?? v?.product ?? null;
  const name = job.design?.name ?? (noun ? noun.replace(/^./, (ch) => ch.toUpperCase()) : job.fileName);
  const size = job.analysis?.bbox_mm;
  const trouble = job.status === 'failed' || job.status === 'blocked';
  const identity = v?.identity ?? job.comparables?.identity ?? null;

  return (
    <div>
      <StatusText status={job.status} stage={job.stage} />

      <h1 className="mt-3 font-display text-[2rem] font-semibold leading-[1.1] tracking-tight [overflow-wrap:anywhere]">{name}</h1>
      <p className="mt-1.5 font-sans text-sm text-slate [overflow-wrap:anywhere]">
        {[name !== job.fileName ? job.fileName : null, identity && identity.object !== noun ? `Read as ${identity.object}` : null].filter(Boolean).join(' · ')}
      </p>
      {identity?.confidence === 'low' ? (
        <p className="mt-4 flex gap-2.5 rounded-xl bg-destructive/8 px-3.5 py-3 font-sans text-sm leading-relaxed text-destructive">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>Research was not sure what this is. Confirm the object before trusting the market or the price.</span>
        </p>
      ) : null}

      <div className="mt-6">
        {headline != null ? (
          <>
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="font-display text-[2.75rem] font-semibold leading-none tabular-nums tracking-tight">{money(headline)}</span>
              {approved ? (
                <span className="inline-flex items-center gap-1 font-sans text-sm font-medium text-ok">
                  <Check className="size-4" aria-hidden="true" />
                  Approved
                </span>
              ) : (
                <span className="font-sans text-sm text-slate">recommended</span>
              )}
            </div>
            {margin ? (
              <p className="mt-2 font-sans text-sm text-slate">
                You keep <span className="font-medium text-navy">{money(margin.usd)}</span> a sale
                {margin.pct != null ? `, a ${pct(margin.pct)} margin` : ''}.
              </p>
            ) : null}
          </>
        ) : p ? (
          <p className="font-display text-2xl font-semibold tracking-tight text-destructive">No sellable price</p>
        ) : job.price != null ? (
          <>
            <div className="flex items-baseline gap-3">
              <span className="font-display text-[2.75rem] font-semibold leading-none tabular-nums tracking-tight">{money(job.price)}</span>
              <span className="font-sans text-sm text-slate">to make one on dapi</span>
            </div>
            <p className="mt-2 font-sans text-sm text-slate">The sale price comes after market research.</p>
          </>
        ) : (
          <p className="flex items-center gap-2.5 font-sans text-[0.9375rem] text-slate">
            {isLive(job) ? <Spinner className="size-4" /> : null}
            {isLive(job) ? 'Not priced yet. This page updates itself.' : 'Not priced.'}
          </p>
        )}
      </div>

      {job.reason && (trouble || (!approved && headline == null)) ? (
        <p
          className={cn(
            'mt-5 flex gap-2.5 rounded-xl px-3.5 py-3 font-sans text-sm leading-relaxed',
            job.status === 'failed' ? 'bg-destructive/8 text-destructive' : 'bg-ice/20 text-navy',
          )}
        >
          <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          {/* The headline already says there is no price; the note keeps only the why. */}
          <span className="first-letter:uppercase">{headline == null ? job.reason.replace(/^no sellable price:\s*/i, '') : job.reason}</span>
        </p>
      ) : null}

      {p ? (
        <div className="mt-6">
          <div className="flex flex-wrap gap-3">
            {!approved && recommended != null ? (
              <Button size="lg" className="h-11 flex-1 px-6 text-[0.9375rem]" disabled={approve.isPending || job.status === 'running'} onClick={() => approve.mutate({ price: recommended })}>
                {approve.isPending ? <Spinner className="size-4" /> : null}
                Approve {money(recommended)}
              </Button>
            ) : null}
            <Button
              size="lg"
              variant={approved || recommended != null ? 'outline' : 'default'}
              className={cn('h-11 px-6 text-[0.9375rem]', !approved && recommended == null && 'flex-1')}
              onClick={() => setDialog(true)}
            >
              {approved ? 'Change price' : recommended != null ? 'Set another price' : 'Set a price'}
            </Button>
          </div>
          {approve.isError ? <p className="mt-2 font-sans text-sm text-destructive">{errorText(approve.error)}</p> : null}
          <p className="mt-3 font-sans text-xs leading-relaxed text-slate">
            {approved
              ? `Approved by ${approved.by}${approved.source === 'override' ? ', overriding the recommendation' : ''}. It is in the catalogue.`
              : 'Approving adds it to the catalogue and starts photos and video.'}
          </p>
          {dialog ? <PriceDialog job={job} open onOpenChange={setDialog} /> : null}
        </div>
      ) : null}

      <dl className="mt-7 border-y border-mist">
        {unitCost != null ? (
          <Fact label="Cost to make">
            {money(unitCost)}
            {v?.costBasis === 'full_plate' && job.price != null && Math.abs(job.price - unitCost) > 0.005 ? (
              <span className="text-slate"> on a full plate · {money(job.price)} alone</span>
            ) : null}
          </Fact>
        ) : job.price != null && headline != null ? (
          <Fact label="Cost to make">{money(job.price)}</Fact>
        ) : null}
        {v ? (
          <Fact label="Market">
            <span className={cn('font-medium', VERDICT[v.verdict].tone)}>{VERDICT[v.verdict].label}</span>
          </Fact>
        ) : null}
        {v?.market ? (
          <Fact label="Etsy sells it for">
            {money(v.market.median)} <span className="text-slate">median of {v.market.n}</span>
          </Fact>
        ) : null}
        {size ? <Fact label="Size">{size.map((n) => n.toFixed(0)).join(' × ')} mm</Fact> : null}
        {job.metrics ? <Fact label="Weight">{job.metrics.model_g.toFixed(1)} g</Fact> : null}
        {job.metrics ? <Fact label="Print time">{formatMinutes(job.metrics.print_time_s)}</Fact> : null}
      </dl>
    </div>
  );
}
