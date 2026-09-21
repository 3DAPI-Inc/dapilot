import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '@/api/client';
import { decidePrice, previewPrice, type Job, type PriceCandidate } from '@/api/jobs';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Spinner } from '@/components/site/Spinner';
import { cn } from '@/lib/utils';

/*
  The price gate. The pipeline recommends one price; a person confirms it or
  sets another, and nothing paid (renders, generation, the product record)
  happens before that. The browser does no pricing arithmetic: what a typed
  price earns comes from the server, which asks pricing.py, and the server's
  check is what accepts or refuses it. The floor holds for people too.
*/

const eyebrow = 'font-sans text-[0.6875rem] uppercase tracking-[0.22em] text-slate/60';
const money = (n: number) => `$${n.toFixed(2)}`;
const pct = (n: number | null | undefined) => (n == null ? '' : `${Math.round(n * 100)}% margin`);
const NAME_KEY = 'dapilot.approver';

function storedName(): string {
  try {
    return localStorage.getItem(NAME_KEY) ?? '';
  } catch {
    return '';
  }
}

function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    try {
      const detail = JSON.parse(e.message).detail;
      if (typeof detail === 'string') return detail;
    } catch {
      /* not JSON: fall through to the raw text */
    }
    return e.message;
  }
  return e instanceof Error ? e.message : 'The request failed.';
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function PriceDecision({ job }: { job: Job }) {
  const p = job.pricing;
  const client = useQueryClient();
  const recommended = p?.proposal && !p.proposal.override ? p.proposal.price : p?.proposal?.proposed?.price ?? null;
  const current = p?.proposal?.price ?? null;
  const [text, setText] = useState(current != null ? current.toFixed(2) : '');
  const [reason, setReason] = useState('');
  const [by, setBy] = useState(storedName);

  const typed = Number.parseFloat(text);
  const valid = Number.isFinite(typed) && typed > 0;
  const debounced = useDebounced(valid ? Math.round(typed * 100) / 100 : null, 300);

  const preview = useQuery({
    queryKey: ['job', job.id, 'price-preview', debounced],
    queryFn: () => previewPrice(job.id, debounced as number),
    enabled: debounced != null,
    staleTime: 60_000,
  });

  const decide = useMutation({
    mutationFn: () => {
      try {
        localStorage.setItem(NAME_KEY, by.trim());
      } catch {
        /* a private window: the name is simply not remembered */
      }
      return decidePrice(job.id, { price: Math.round(typed * 100) / 100, by: by.trim() || undefined, reason: reason.trim() || undefined });
    },
    onSuccess: (res) => {
      client.setQueryData(['job', job.id], res.job);
      client.invalidateQueries({ queryKey: ['products'] });
      client.invalidateQueries({ queryKey: ['job', job.id, 'events'] });
    },
  });

  if (!p) return null;
  const approved = p.approved;
  const floor = p.floor;
  const isCandidate = (c: PriceCandidate) => valid && Math.abs(c.price - typed) < 0.005;
  const matchesRecommendation = valid && recommended != null && Math.abs(recommended - typed) < 0.005;
  const custom = valid && !p.candidates.some(isCandidate);
  const belowFloor = valid && floor != null && typed < floor;
  const needsReason = custom && reason.trim().length === 0;
  const busy = decide.isPending || job.status === 'running';

  return (
    <section className="mt-14">
      <h2 className="font-display text-[0.6875rem] font-semibold uppercase tracking-[0.22em] text-slate/60">Sale price</h2>

      {approved ? (
        <p className="mt-3 font-sans text-sm text-slate">
          <span className="text-navy">{money(approved.price)} approved</span> by {approved.by}
          {approved.source === 'override' ? ', overriding the recommendation' : ', as recommended'}
          {approved.margin_pct != null ? ` · ${pct(approved.margin_pct)}` : ''}. The product is in the catalogue and the rest of intake is queued.
        </p>
      ) : null}

      <div className="mt-5 flex flex-col gap-6 sm:flex-row sm:items-start sm:gap-12">
        <div className="shrink-0">
          <div className="font-display text-[2.75rem] font-semibold leading-none tabular-nums tracking-tight text-navy">
            {recommended != null ? money(recommended) : 'None'}
          </div>
          <div className={cn(eyebrow, 'mt-2')}>
            {recommended != null ? `recommended · ${(p.proposal?.proposed ?? p.proposal)?.candidate} candidate` : 'no recommendation'}
          </div>
        </div>
        <div className="min-w-0">
          {p.proposal ? (
            <>
              <p className="max-w-[58ch] font-sans text-sm leading-relaxed text-navy">
                {(p.proposal.proposed ?? p.proposal).rationale}
              </p>
              {(p.proposal.proposed ?? p.proposal).runner_up ? (
                <p className="mt-2 max-w-[58ch] font-sans text-xs leading-relaxed text-slate">
                  Runner-up {(p.proposal.proposed ?? p.proposal).runner_up!.candidate} at{' '}
                  {money((p.proposal.proposed ?? p.proposal).runner_up!.price)}: {(p.proposal.proposed ?? p.proposal).runner_up!.why_not}
                </p>
              ) : null}
              {p.proposal.override ? (
                <p className="mt-2 max-w-[58ch] font-sans text-xs leading-relaxed text-slate">
                  Overridden to {money(p.proposal.price)} by {p.proposal.override.by}: {p.proposal.override.reason}
                </p>
              ) : null}
            </>
          ) : (
            <p className="max-w-[58ch] font-sans text-sm leading-relaxed text-destructive">
              {p.rejected ?? 'No price chosen yet.'} You can still set one at or above the floor.
            </p>
          )}
        </div>
      </div>

      {/* The allowed prices, one click each; rejected ones are shown but cannot be picked. */}
      <ul className="mt-6 border-t border-mist font-sans text-sm">
        {[...p.candidates, ...p.rejectedCandidates].map((c) => {
          const dead = Boolean(c.rejected_because);
          return (
            <li key={c.label} className="border-b border-mist">
              <button
                type="button"
                disabled={dead || busy}
                onClick={() => setText(c.price.toFixed(2))}
                aria-pressed={isCandidate(c)}
                className={cn(
                  'grid w-full grid-cols-[6rem_5rem_1fr] items-baseline gap-x-4 py-2.5 text-left sm:grid-cols-[6rem_5rem_8rem_1fr]',
                  !dead && 'cursor-pointer hover:bg-mist/40',
                  isCandidate(c) && !dead && 'bg-mist/60',
                )}
              >
                <span className="text-navy">{c.label}</span>
                <span className="tabular-nums text-navy">{money(c.price)}</span>
                <span className="tabular-nums text-slate">{pct(c.margin_pct)}</span>
                <span className={cn('col-span-3 sm:col-span-1', dead ? 'text-destructive' : 'text-slate')}>
                  {dead
                    ? c.rejected_because === 'below_floor'
                      ? `not allowed: ${money(c.below_floor_by ?? 0)} below the floor`
                      : 'under the minimum margin; needs a reason to choose'
                    : (c.position_in_band ?? '').replace(/_/g, ' ')}
                </span>
              </button>
            </li>
          );
        })}
        {p.skipped.map((s) => (
          <li key={s.label} className="grid grid-cols-[6rem_1fr] gap-x-4 border-b border-mist py-2.5 text-slate/80">
            <span>{s.label}</span>
            <span>not offered: {s.reason}</span>
          </li>
        ))}
      </ul>

      {p.p75Sellers ? (
        <p className="mt-2 font-sans text-xs text-slate/80">
          At or above the p75: {p.p75Sellers.distinct_named_shops} named shop{p.p75Sellers.distinct_named_shops === 1 ? '' : 's'}, the
          largest holding {Math.round(p.p75Sellers.top_share * 100)}% of the sales weight.
        </p>
      ) : null}

      <form
        className="mt-6 grid gap-4 sm:grid-cols-[10rem_1fr]"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid && !belowFloor && !needsReason) decide.mutate();
        }}
      >
        <label className="block">
          <span className={eyebrow}>your price, {p.currency}</span>
          <Input
            inputMode="decimal"
            value={text}
            onChange={(e) => setText(e.target.value)}
            aria-invalid={belowFloor || (!valid && text !== '')}
            className="mt-1.5 tabular-nums"
          />
        </label>
        <div className="self-end font-sans text-sm">
          {belowFloor ? (
            <span className="text-destructive">
              Below the floor of {money(floor!)}: at this price each sale loses money, so it is refused for anyone.
            </span>
          ) : preview.data && valid ? (
            <span className="text-slate">
              <span className="text-navy">{money(preview.data.margin)} per sale</span>, {pct(preview.data.margin_pct)} · net of VAT{' '}
              {money(preview.data.net_of_vat)}, fees {money(preview.data.fees)}, cost {money(preview.data.unit_cost)}
              {floor != null ? ` · floor ${money(floor)}` : ''}
            </span>
          ) : floor != null ? (
            <span className="text-slate">Floor {money(floor)}: cost, platform fees and VAT owed.</span>
          ) : null}
        </div>

        {valid && !matchesRecommendation ? (
          <>
            <label className="block">
              <span className={eyebrow}>your name</span>
              <Input value={by} onChange={(e) => setBy(e.target.value)} placeholder="console" className="mt-1.5" />
            </label>
            <label className="block">
              <span className={eyebrow}>why this price{custom ? '' : ' (optional)'}</span>
              <Input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder={custom ? 'Required for a price that is not a candidate' : 'Kept with the price'}
                className="mt-1.5"
              />
            </label>
          </>
        ) : null}

        <div className="flex items-center gap-4 sm:col-span-2">
          <Button type="submit" disabled={!valid || belowFloor || needsReason || busy}>
            {decide.isPending ? <Spinner className="size-4" /> : null}
            {approved
              ? 'Change the approved price'
              : matchesRecommendation
                ? `Approve ${money(typed)}`
                : valid
                  ? `Override with ${money(typed)}`
                  : 'Approve'}
          </Button>
          {decide.isError ? <span className="font-sans text-sm text-destructive">{errorText(decide.error)}</span> : null}
          {!approved && !decide.isError ? (
            <span className="font-sans text-xs text-slate/80">
              Approving files the product in the catalogue and queues photos, video and everything after.
            </span>
          ) : null}
        </div>
      </form>
    </section>
  );
}
