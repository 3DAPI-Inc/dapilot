import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { listProducts, type Product } from '@/api/jobs';
import { ConsoleNav, SiteHeader } from '@/components/site/SiteHeader';
import { Spinner } from '@/components/site/Spinner';
import { STATUS_LABEL } from '@/lib/stages';
import { cn } from '@/lib/utils';

/*
  The catalogue: every item whose price a person approved. A product appears
  here the moment its price is set, before it has a single photo, because the
  approval is what makes it a product; the media arrives as the rest of intake
  runs. The record sits between dapi, who makes it from its quote, and the
  Etsy listing that sells it.
*/

const eyebrow = 'font-sans text-[0.6875rem] uppercase tracking-[0.22em] text-slate/60';
const money = (n: number) => `$${n.toFixed(2)}`;

function mediaLine(p: Product): string {
  if (p.status === 'ready') return `${p.photos.length} photo${p.photos.length === 1 ? '' : 's'}${p.video ? ' and a video' : ''}`;
  const s = p.pipeline;
  if (s.status === 'running') return `${s.stage} running: photos and video on the way`;
  if (s.status === 'failed') return `${s.stage} failed${s.reason ? `: ${s.reason}` : ''}`;
  if (s.status === 'blocked') return `${s.stage} ${STATUS_LABEL.blocked}${s.reason ? `: ${s.reason}` : ''}`;
  return 'photos and video queued';
}

function ProductCard({ p }: { p: Product }) {
  return (
    <Link to={`/jobs/${p.id}`} className="group block">
      <div className="aspect-square overflow-hidden rounded-2xl border border-mist bg-white">
        {p.thumbnail ? (
          <img src={p.thumbnail} alt="" loading="lazy" className={cn('size-full object-cover', p.status !== 'ready' && 'opacity-70')} />
        ) : (
          <div className="flex size-full items-center justify-center font-sans text-sm text-slate/70">No picture yet</div>
        )}
      </div>
      <div className="mt-3 flex items-baseline justify-between gap-3">
        <h2 className="min-w-0 truncate font-display text-lg font-semibold tracking-tight text-navy underline-offset-4 group-hover:underline">
          {p.name}
        </h2>
        <span className="shrink-0 font-display text-lg font-semibold tabular-nums text-navy">{money(p.price.price)}</span>
      </div>
      <p className="mt-1 font-sans text-xs leading-relaxed text-slate">
        {p.price.margin_pct != null ? `${Math.round(p.price.margin_pct * 100)}% margin on ${money(p.price.unit_cost)} cost` : null}
        {p.price.source === 'override' ? ` · set by ${p.price.by}` : ' · as recommended'}
      </p>
      <p className={cn('mt-1 font-sans text-xs', p.pipeline.status === 'failed' ? 'text-destructive' : 'text-slate/80')}>
        {mediaLine(p)}
      </p>
      <p className={cn(eyebrow, 'mt-2')}>
        {p.storefront.state === 'not_listed' ? `not on ${p.storefront.platform} yet` : `${p.storefront.platform} · ${p.storefront.state}`}
        {' · '}quote {p.quote.publicId}
      </p>
    </Link>
  );
}

export default function Catalog() {
  const products = useQuery({
    queryKey: ['products'],
    queryFn: listProducts,
    // Media lands on its own while the design stage runs.
    refetchInterval: (q) => (q.state.data?.some((p) => p.status !== 'ready' && p.pipeline.status !== 'failed') ? 5000 : false),
  });
  const unverified = products.data?.find((p) => !p.owner.verified);

  return (
    <div className="min-h-svh bg-paper text-navy">
      <SiteHeader>
        <ConsoleNav />
      </SiteHeader>
      <main className="mx-auto max-w-[940px] px-5 pb-24 sm:px-10">
        <h1 className="font-display text-4xl font-semibold tracking-tight">Catalogue</h1>
        <p className="mt-3 max-w-[58ch] font-sans leading-relaxed text-slate">
          Products with an approved price. Each one holds the dapi quote an order is made from, so an
          order for a customer can be placed from here on. Photos and video arrive as intake finishes.
        </p>

        {products.isPending ? (
          <p className="flex items-center gap-2 py-10 font-sans text-sm text-slate">
            <Spinner className="size-4" />
            Loading the catalogue.
          </p>
        ) : products.isError ? (
          <p className="py-10 font-sans text-sm text-destructive">
            The catalogue did not load: {products.error instanceof Error ? products.error.message : 'the request failed'}.
          </p>
        ) : products.data.length === 0 ? (
          <p className="mt-10 max-w-[52ch] font-sans text-sm leading-relaxed text-slate">
            Nothing here yet. A product appears once you approve a price on one of the{' '}
            <Link to="/agent-runs" className="text-navy underline underline-offset-4">agent runs</Link>.
          </p>
        ) : (
          <>
            {unverified ? (
              <p className="mt-6 max-w-[62ch] font-sans text-xs leading-relaxed text-destructive">
                Some products are not tied to a verified dapi API key{unverified.owner.note ? ` (${unverified.owner.note})` : ''}.
                Orders cannot be placed for them until the server reaches dapi with a working key.
              </p>
            ) : null}
            <div className="mt-10 grid gap-x-6 gap-y-10 sm:grid-cols-2 lg:grid-cols-3">
              {products.data.map((p) => (
                <ProductCard key={p.id} p={p} />
              ))}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
