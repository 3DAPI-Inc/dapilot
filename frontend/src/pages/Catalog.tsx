import { ConsoleNav, SiteHeader } from '@/components/site/SiteHeader';

export default function Catalog() {
  return (
    <div className="min-h-svh bg-paper text-navy">
      <SiteHeader>
        <ConsoleNav />
      </SiteHeader>
      <main className="mx-auto max-w-[940px] px-5 pb-24 sm:px-10">
        <h1 className="font-display text-4xl font-semibold tracking-tight">Catalogue</h1>
        <p className="mt-3 max-w-[52ch] font-sans leading-relaxed text-slate">
          Published designs land here. The public API has no route that reads or creates a
          design yet, so there is nothing to list.
        </p>
      </main>
    </div>
  );
}
