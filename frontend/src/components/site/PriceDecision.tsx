import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '@/api/client';
import { decidePrice, previewPrice, type Job, type PriceCandidate, type Pricing } from '@/api/jobs';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Spinner } from '@/components/site/Spinner';
import { money } from '@/lib/format';
import { cn } from '@/lib/utils';

/*
  The price gate. The pipeline recommends one price; a person confirms it or
  sets another, and nothing paid (renders, generation, the product record)
  happens before that. The browser does no pricing arithmetic: what a typed
  price earns comes from the server, which asks pricing.py, and the server's
  check is what accepts or refuses it. The floor holds for people too.

  Approving the recommendation is one click on the product page. Anything else
  happens in the dialog below, which is the only place a price can be typed.
*/

const label = 'font-sans text-xs font-medium text-slate';
export const pct = (n: number | null | undefined) => (n == null ? '' : `${Math.round(n * 100)}%`);
const NAME_KEY = 'dapilot.approver';

function storedName(): string {
  try {
    return localStorage.getItem(NAME_KEY) ?? '';
  } catch {
    return '';
  }
}

export function errorText(e: unknown): string {
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

/** The model's own recommendation, which survives a person overriding it. */
export const recommendedPrice = (p: Pricing | undefined): number | null =>
  p?.proposal && !p.proposal.override ? p.proposal.price : p?.proposal?.proposed?.price ?? null;

/** One way to send a decision, so the one-click approve and the dialog cannot drift. */
export function useDecidePrice(job: Job, onDone?: () => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { price: number; by?: string; reason?: string }) =>
      decidePrice(job.id, { price: Math.round(body.price * 100) / 100, by: body.by?.trim() || storedName() || undefined, reason: body.reason?.trim() || undefined }),
    onSuccess: (res) => {
      client.setQueryData(['job', job.id], res.job);
      client.invalidateQueries({ queryKey: ['products'] });
      client.invalidateQueries({ queryKey: ['jobs'] });
      client.invalidateQueries({ queryKey: ['job', job.id, 'events'] });
      onDone?.();
    },
  });
}

function CandidateOption({ c, picked, disabled, onPick }: { c: PriceCandidate; picked: boolean; disabled: boolean; onPick: () => void }) {
  const dead = Boolean(c.rejected_because);
  return (
    <button
      type="button"
      disabled={dead || disabled}
      onClick={onPick}
      aria-pressed={picked}
      className={cn(
        'rounded-xl border px-3.5 py-3 text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ice',
        dead ? 'cursor-not-allowed border-mist bg-mist/30' : picked ? 'border-navy bg-mist/50' : 'border-mist hover:border-slate/40',
      )}
    >
      <span className="block font-sans text-xs capitalize text-slate">{c.label}</span>
      <span className={cn('mt-0.5 block font-display text-lg font-semibold tabular-nums tracking-tight', dead ? 'text-slate/60 line-through' : 'text-navy')}>
        {money(c.price)}
      </span>
      <span className={cn('mt-0.5 block font-sans text-xs', dead ? 'text-destructive' : 'text-slate')}>
        {dead
          ? c.rejected_because === 'below_floor'
            ? `${money(c.below_floor_by ?? 0)} below the floor`
            : 'Under the minimum margin'
          : `${pct(c.margin_pct)} margin`}
      </span>
    </button>
  );
}

export function PriceDialog({ job, open, onOpenChange }: { job: Job; open: boolean; onOpenChange: (open: boolean) => void }) {
  const p = job.pricing;
  const recommended = recommendedPrice(p);
  const current = p?.approved?.price ?? p?.proposal?.price ?? null;
  const [text, setText] = useState(current != null ? current.toFixed(2) : '');
  const [reason, setReason] = useState('');
  const [by, setBy] = useState(storedName);

  const typed = Number.parseFloat(text);
  const valid = Number.isFinite(typed) && typed > 0;
  const debounced = useDebounced(valid ? Math.round(typed * 100) / 100 : null, 300);

  const preview = useQuery({
    queryKey: ['job', job.id, 'price-preview', debounced],
    queryFn: () => previewPrice(job.id, debounced as number),
    enabled: open && debounced != null,
    staleTime: 60_000,
  });

  const decide = useDecidePrice(job, () => onOpenChange(false));

  if (!p) return null;
  const floor = p.floor;
  const isCandidate = (c: PriceCandidate) => valid && Math.abs(c.price - typed) < 0.005;
  const matchesRecommendation = valid && recommended != null && Math.abs(recommended - typed) < 0.005;
  const custom = valid && !p.candidates.some(isCandidate);
  const belowFloor = valid && floor != null && typed < floor;
  const needsReason = custom && reason.trim().length === 0;
  const busy = decide.isPending || job.status === 'running';
  const options = [...p.candidates, ...p.rejectedCandidates];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[34rem]">
        <DialogHeader>
          <DialogTitle className="font-display text-xl tracking-tight">Set the sale price</DialogTitle>
          <DialogDescription>
            {floor != null ? `Anything from ${money(floor)} up. Below that each sale loses money, so it is refused for anyone.` : 'Pick a candidate or type your own.'}
          </DialogDescription>
        </DialogHeader>

        <form
          className="grid gap-5"
          onSubmit={(e) => {
            e.preventDefault();
            if (!valid || belowFloor || needsReason) return;
            try {
              localStorage.setItem(NAME_KEY, by.trim());
            } catch {
              /* a private window: the name is simply not remembered */
            }
            decide.mutate({ price: typed, by, reason });
          }}
        >
          {options.length > 0 ? (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {options.map((c) => (
                <CandidateOption key={c.label} c={c} picked={isCandidate(c)} disabled={busy} onPick={() => setText(c.price.toFixed(2))} />
              ))}
            </div>
          ) : null}

          <div className="grid gap-4 sm:grid-cols-[9rem_1fr] sm:items-end">
            <label className="block">
              <span className={label}>Your price, {p.currency}</span>
              <Input
                inputMode="decimal"
                value={text}
                onChange={(e) => setText(e.target.value)}
                aria-invalid={belowFloor || (!valid && text !== '')}
                className="mt-1.5 h-11 font-display text-lg font-semibold tabular-nums"
              />
            </label>
            <div className="min-h-11 font-sans text-sm">
              {belowFloor ? (
                <p className="text-destructive">Below the floor of {money(floor!)}.</p>
              ) : preview.data && valid ? (
                <dl className="grid grid-cols-4 gap-2 text-xs text-slate">
                  {(
                    [
                      ['After VAT', money(preview.data.net_of_vat)],
                      ['Fees', money(preview.data.fees)],
                      ['Cost', money(preview.data.unit_cost)],
                      ['You keep', `${money(preview.data.margin)}`],
                    ] as const
                  ).map(([k, v], i) => (
                    <div key={k}>
                      <dt>{k}</dt>
                      <dd className={cn('mt-0.5 text-sm tabular-nums', i === 3 ? 'font-semibold text-navy' : 'text-navy')}>
                        {v}
                        {i === 3 && preview.data.margin_pct != null ? <span className="ml-1 font-normal text-slate">{pct(preview.data.margin_pct)}</span> : null}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : preview.isFetching ? (
                <p className="flex items-center gap-2 text-slate"><Spinner className="size-3.5" />Working out the margin.</p>
              ) : null}
            </div>
          </div>

          {valid && !matchesRecommendation ? (
            <div className="grid gap-4 sm:grid-cols-[9rem_1fr]">
              <label className="block">
                <span className={label}>Your name</span>
                <Input value={by} onChange={(e) => setBy(e.target.value)} placeholder="console" className="mt-1.5" />
              </label>
              <label className="block">
                <span className={label}>Why this price{custom ? '' : ' (optional)'}</span>
                <Input
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder={custom ? 'Required for a price that is not a candidate' : 'Kept with the price'}
                  className="mt-1.5"
                />
              </label>
            </div>
          ) : null}

          {decide.isError ? <p className="font-sans text-sm text-destructive">{errorText(decide.error)}</p> : null}

          <div className="flex items-center justify-end gap-3">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!valid || belowFloor || needsReason || busy}>
              {decide.isPending ? <Spinner className="size-4" /> : null}
              {!valid ? 'Approve' : p.approved ? `Change to ${money(typed)}` : matchesRecommendation ? `Approve ${money(typed)}` : `Override with ${money(typed)}`}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
