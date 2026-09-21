import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ImageOff, TriangleAlert } from 'lucide-react';
import { listProducts, type Product } from '@/api/jobs';
import { ConsoleShell, Notice, PageHeader, Panel } from '@/components/site/ConsoleShell';
import { Spinner } from '@/components/site/Spinner';
import { money } from '@/lib/format';

/*
  The catalogue: every item whose price a person approved. A product appears
  here the moment its price is set, before it has a single photo, because the
  approval is what makes it a product; the media arrives as the rest of intake
  runs. A card is a shop card: picture, name, price, and one word on where it
  stands. The rest is on the product page.
*/

function standing(p: Product): { label: string; tone: string; title?: string } {
  if (p.status === 'ready') return { label: p.storefront.state === 'not_listed' ? 'ready to list' : `on ${p.storefront.platform}`, tone: 'text-ok' };
  const s = p.pipeline;
  if (s.status === 'failed') return { label: `${s.stage} failed`, tone: 'text-destructive', title: s.reason ?? undefined };
  if (s.status === 'blocked') return { label: 'needs you', tone: 'font-medium text-navy', title: s.reason ?? undefined };
  if (s.status === 'running') return { label: 'making photos', tone: 'text-navy' };
  return { label: 'photos queued', tone: 'text-slate' };
}

function ProductCard({ p }: { p: Product }) {
  const s = standing(p);
  return (
    <Link to={`/jobs/${p.id}`} className="group block rounded-2xl focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ice">
      <div className="relative aspect-square overflow-hidden rounded-2xl border border-mist bg-white shadow-card">
        {p.thumbnail ? (
          <img
            src={p.thumbnail}
            alt=""
            loading="lazy"
            className="size-full object-cover transition-transform duration-300 group-hover:scale-[1.03] motion-reduce:transition-none motion-reduce:group-hover:scale-100"
          />
        ) : (
          <div className="flex size-full items-center justify-center">
            <ImageOff className="size-6 text-slate/30" aria-hidden="true" />
          </div>
        )}
      </div>
      <div className="mt-3 flex items-baseline justify-between gap-3">
        <h2 className="min-w-0 truncate font-sans text-[0.9375rem] font-medium text-navy underline-offset-4 group-hover:underline">{p.name}</h2>
        <span className="shrink-0 font-display text-[1.0625rem] font-semibold tabular-nums tracking-tight text-navy">{money(p.price.price)}</span>
      </div>
      <p className="mt-0.5 font-sans text-[0.8125rem] text-slate">
        {p.price.margin_pct != null ? `${Math.round(p.price.margin_pct * 100)}% margin` : `${money(p.price.margin)} a sale`}
        <span aria-hidden="true"> · </span>
        <span title={s.title} className={s.tone}>{s.label}</span>
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
  const count = products.data?.length ?? 0;

  return (
    <ConsoleShell>
      <PageHeader title="Catalogue" note={count > 0 ? `${count} product${count === 1 ? '' : 's'} with an approved price.` : undefined} />

      {products.isPending ? (
        <p className="flex items-center gap-2 py-16 font-sans text-sm text-slate">
          <Spinner className="size-4" />
          Loading the catalogue.
        </p>
      ) : products.isError ? (
        <Panel className="mt-6">
          <Notice title="The catalogue did not load" tone="bad">
            {products.error instanceof Error ? products.error.message : 'The request failed.'}
          </Notice>
        </Panel>
      ) : products.data.length === 0 ? (
        <Panel className="mt-6">
          <Notice title="No products yet">
            A product appears here once you approve its price on one of the{' '}
            <Link to="/agent-runs" className="text-navy underline underline-offset-4">agent runs</Link>.
          </Notice>
        </Panel>
      ) : (
        <>
          {unverified ? (
            <p className="mt-6 flex gap-2.5 rounded-xl bg-destructive/8 px-3.5 py-3 font-sans text-sm leading-relaxed text-destructive">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <span>
                Some products are not tied to a verified dapi API key{unverified.owner.note ? ` (${unverified.owner.note})` : ''}. Orders
                cannot be placed for them until the server reaches dapi with a working key.
              </span>
            </p>
          ) : null}
          <div className="mt-8 grid grid-cols-2 gap-x-5 gap-y-9 md:grid-cols-3 lg:grid-cols-4">
            {products.data.map((p) => (
              <ProductCard key={p.id} p={p} />
            ))}
          </div>
        </>
      )}
    </ConsoleShell>
  );
}
