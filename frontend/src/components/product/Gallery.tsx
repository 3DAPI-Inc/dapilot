import { useState } from 'react';
import { Expand, ImageOff } from 'lucide-react';
import type { Job } from '@/api/jobs';
import { Lightbox, Thumb, type Media } from '@/components/site/Lightbox';
import { Spinner } from '@/components/site/Spinner';
import { cn } from '@/lib/utils';

/*
  The product's pictures, the way a shop shows them: one large, the rest as a
  strip, all of them one click from full size. It shows the best set the work
  dir holds: the listing media in Etsy order once design has run, otherwise
  the preview drafts, and it says which of the two you are looking at.
*/

export function mediaOf(job: Job): { items: Media[]; drafts: boolean } {
  const d = job.design;
  const name = d?.name ?? job.fileName;
  if (d && d.photos.length > 0) {
    const items: Media[] = d.photos.map((p, i) => ({ kind: 'image', src: p.url, label: `${name}, photo ${i + 1} (${p.kind})` }));
    if (d.video) {
      items.splice(1, 0, { kind: 'video', src: d.video.url, label: `Listing video, ${d.video.id === 'turntable' ? 'studio turntable' : 'lifestyle clip'}` });
    }
    // The listing video is the lifestyle clip when it was kept; the turntable is still worth seeing.
    if (d.turntable && d.video?.id !== 'turntable') items.push({ kind: 'video', src: d.turntable, label: 'Studio turntable' });
    return { items, drafts: false };
  }
  const r = job.renders;
  const items: Media[] = [];
  if (r?.hero) items.push({ kind: 'image', src: r.hero, label: `${name}, hero render` });
  r?.angles.forEach((src, i) => items.push({ kind: 'image', src, label: `${name}, angle ${i + 1}` }));
  if (r?.video) items.push({ kind: 'video', src: r.video, label: 'Turntable' });
  r?.drafts.forEach((src, i) => items.push({ kind: 'image', src, label: `${name}, draft ${i + 1} of ${r.drafts.length}` }));
  const ours = items.length;
  // The designer's own previews out of a 3MF. Last, because they are not our pictures.
  (job.thumbnails ?? []).forEach((src) => items.push({ kind: 'image', src, label: "The designer's own preview, from the 3MF" }));
  return { items, drafts: ours > 0 && !r?.hero };
}

export function Gallery({ job }: { job: Job }) {
  const { items, drafts } = mediaOf(job);
  const [at, setAt] = useState(0);
  const [viewing, setViewing] = useState<number | null>(null);
  const index = Math.min(at, Math.max(items.length - 1, 0));
  const main = items[index];

  if (!main) {
    const waiting = job.status === 'running' || job.status === 'queued';
    return (
      <div className="flex aspect-square w-full flex-col items-center justify-center gap-3 rounded-2xl border border-mist bg-white text-center shadow-card">
        {waiting ? <Spinner /> : <ImageOff className="size-6 text-slate/40" aria-hidden="true" />}
        <p className="max-w-[28ch] font-sans text-sm text-slate">
          {waiting ? 'No pictures yet. The first drafts arrive with the preview stage.' : 'No pictures were made for this model.'}
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="group relative overflow-hidden rounded-2xl border border-mist bg-white shadow-card">
        {main.kind === 'video' ? (
          <video key={main.src} src={main.src} controls muted loop playsInline className="aspect-square w-full bg-white object-contain" />
        ) : (
          <button
            type="button"
            onClick={() => setViewing(index)}
            aria-label={`Open ${main.label} full size`}
            className="block w-full cursor-zoom-in focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ice"
          >
            <img src={main.src} alt={main.label} className="aspect-square w-full object-cover" />
          </button>
        )}
        <button
          type="button"
          onClick={() => setViewing(index)}
          aria-label="Open full size"
          className="absolute right-3 bottom-3 inline-flex size-9 items-center justify-center rounded-full bg-paper/90 text-navy opacity-0 shadow-hairline backdrop-blur-sm transition-opacity group-hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ice"
        >
          <Expand className="size-4" />
        </button>
      </div>

      {items.length > 1 ? (
        <div className="mt-3 grid grid-cols-6 gap-2 sm:grid-cols-8">
          {items.map((m, i) => (
            <button
              key={m.src}
              type="button"
              onClick={() => setAt(i)}
              onMouseEnter={() => setAt(i)}
              aria-label={m.label}
              aria-current={i === index}
              className={cn(
                'relative aspect-square w-full overflow-hidden rounded-xl border bg-white transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ice',
                i === index ? 'border-navy' : 'border-mist hover:border-slate/40',
              )}
            >
              <Thumb media={m} />
            </button>
          ))}
        </div>
      ) : null}

      {drafts ? <p className="mt-3 font-sans text-xs text-slate">Draft previews. Listing photos come with the design stage.</p> : null}

      <Lightbox items={items} index={viewing} onIndex={setViewing} onClose={() => setViewing(null)} />
    </div>
  );
}
