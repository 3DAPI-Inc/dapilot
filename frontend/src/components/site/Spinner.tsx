import { cn } from '@/lib/utils';

/**
 * The one busy indicator. Always captioned, because under
 * prefers-reduced-motion the ring stops turning and a still ring says nothing.
 */
export function Spinner({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'inline-block size-5 shrink-0 animate-spin rounded-full border-2 border-mist border-t-navy',
        className,
      )}
    />
  );
}
