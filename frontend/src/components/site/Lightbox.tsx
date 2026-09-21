import { useCallback, useEffect, useState, type CSSProperties, type MouseEvent, type ReactNode } from 'react';
import { Dialog as DialogPrimitive } from 'radix-ui';
import { ChevronLeft, ChevronRight, Play, X, ZoomIn, ZoomOut } from 'lucide-react';
import { cn } from '@/lib/utils';

/*
  Every picture in the console opens here. One viewer, so a draft render, a
  listing photo and a competitor's thumbnail all behave the same way: click to
  open, click again to zoom in where you clicked, move to look around, arrows
  to step through the set.
*/

export interface Media {
  kind: 'image' | 'video';
  src: string;
  /** Said under the picture in the viewer, and used as alt text. */
  label: string;
}

const ZOOM = 2.5;
const chrome =
  'inline-flex size-10 items-center justify-center rounded-full bg-white/10 text-white transition-colors hover:bg-white/20 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ice disabled:opacity-30';

export function Lightbox({
  items,
  index,
  onIndex,
  onClose,
}: {
  items: Media[];
  /** null is closed. */
  index: number | null;
  onIndex: (i: number) => void;
  onClose: () => void;
}) {
  const [zoomed, setZoomed] = useState(false);
  const [origin, setOrigin] = useState('50% 50%');
  const open = index != null && items.length > 0;
  const at = open ? Math.min(index, items.length - 1) : 0;
  const item = items[at];

  const step = useCallback(
    (by: number) => {
      if (items.length < 2) return;
      setZoomed(false);
      onIndex((at + by + items.length) % items.length);
    },
    [at, items.length, onIndex],
  );

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') step(1);
      if (e.key === 'ArrowLeft') step(-1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, step]);

  // Zoom never survives a close: the next picture opens whole.
  const close = () => {
    setZoomed(false);
    onClose();
  };

  const follow = (e: MouseEvent<HTMLElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    setOrigin(`${((e.clientX - box.left) / box.width) * 100}% ${((e.clientY - box.top) / box.height) * 100}%`);
  };

  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => (next ? null : close())}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-[oklch(0.16_0.03_259/0.94)] backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          className="fixed inset-0 z-50 flex flex-col outline-none"
          onClick={(e) => {
            if (e.target === e.currentTarget) close();
          }}
        >
          <div className="flex items-center justify-between gap-4 px-4 py-3 sm:px-6">
            <DialogPrimitive.Title className="min-w-0 truncate font-sans text-sm text-white/80">
              {item ? item.label : 'Picture'}
              {items.length > 1 ? <span className="ml-3 tabular-nums text-white/50">{at + 1} / {items.length}</span> : null}
            </DialogPrimitive.Title>
            <div className="flex shrink-0 items-center gap-2">
              {item?.kind === 'image' ? (
                <button type="button" className={chrome} onClick={() => setZoomed((z) => !z)} aria-label={zoomed ? 'Zoom out' : 'Zoom in'}>
                  {zoomed ? <ZoomOut className="size-4.5" /> : <ZoomIn className="size-4.5" />}
                </button>
              ) : null}
              <DialogPrimitive.Close className={chrome} aria-label="Close">
                <X className="size-4.5" />
              </DialogPrimitive.Close>
            </div>
          </div>

          <div
            className="relative flex min-h-0 flex-1 items-center justify-center px-4 sm:px-20"
            onClick={(e) => {
              if (e.target === e.currentTarget) close();
            }}
          >
            {item?.kind === 'video' ? (
              <video key={item.src} src={item.src} controls autoPlay muted loop playsInline className="max-h-full max-w-full rounded-xl bg-black" />
            ) : item ? (
              <div className="max-h-full max-w-full overflow-hidden rounded-xl" onMouseMove={zoomed ? follow : undefined}>
                <img
                  key={item.src}
                  src={item.src}
                  alt={item.label}
                  draggable={false}
                  onClick={(e) => {
                    follow(e);
                    setZoomed((z) => !z);
                  }}
                  // Sized by the viewer, not the file: a 320 px draft fills the room the same as a 2048 px still.
                  style={{ transformOrigin: origin, transform: zoomed ? `scale(${ZOOM})` : undefined, height: 'min(calc(100svh - 11rem), 100vw - 2rem)' } as CSSProperties}
                  className={cn(
                    'block w-auto max-w-full select-none bg-white object-contain transition-transform duration-200 motion-reduce:transition-none',
                    zoomed ? 'cursor-zoom-out' : 'cursor-zoom-in',
                  )}
                />
              </div>
            ) : null}

            {items.length > 1 ? (
              <>
                <button type="button" onClick={() => step(-1)} aria-label="Previous" className={cn(chrome, 'absolute top-1/2 left-3 -translate-y-1/2 sm:left-6')}>
                  <ChevronLeft className="size-5" />
                </button>
                <button type="button" onClick={() => step(1)} aria-label="Next" className={cn(chrome, 'absolute top-1/2 right-3 -translate-y-1/2 sm:right-6')}>
                  <ChevronRight className="size-5" />
                </button>
              </>
            ) : null}
          </div>

          {items.length > 1 ? (
            <div className="flex justify-center gap-2 overflow-x-auto px-4 py-4">
              {items.map((m, i) => (
                <button
                  key={m.src}
                  type="button"
                  onClick={() => {
                    setZoomed(false);
                    onIndex(i);
                  }}
                  aria-label={m.label}
                  aria-current={i === at}
                  className={cn(
                    'relative size-14 shrink-0 overflow-hidden rounded-lg bg-white transition-opacity focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ice',
                    i === at ? 'opacity-100 ring-2 ring-white' : 'opacity-50 hover:opacity-80',
                  )}
                >
                  <Thumb media={m} />
                </button>
              ))}
            </div>
          ) : (
            <div className="h-6" />
          )}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/** A still for a thumbnail slot. A video shows its first frame under a play mark. */
export function Thumb({ media, className }: { media: Media; className?: string }) {
  if (media.kind === 'video') {
    return (
      <>
        <video src={media.src} muted playsInline preload="metadata" className={cn('size-full object-cover', className)} />
        <span className="absolute inset-0 flex items-center justify-center bg-navy/20">
          <Play className="size-4 fill-white text-white" aria-hidden="true" />
        </span>
      </>
    );
  }
  return <img src={media.src} alt="" loading="lazy" className={cn('size-full object-cover', className)} />;
}

/** One picture that opens the viewer on its own, for places that are not a gallery. */
export function Zoomable({ media, className, children }: { media: Media; className?: string; children?: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen(true);
        }}
        aria-label={`Open ${media.label}`}
        className={cn('relative block cursor-zoom-in overflow-hidden focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ice', className)}
      >
        {children ?? <Thumb media={media} />}
      </button>
      <Lightbox items={[media]} index={open ? 0 : null} onIndex={() => {}} onClose={() => setOpen(false)} />
    </>
  );
}
