import type { Identity } from '@/api/jobs';

/*
  What research decided the object is, and the pictures the file itself carried.

  The filename is one witness among four (thumbnails, drafts, analysis, filename);
  a `low` reading means the market band and the price below may belong to another
  product, so it is said in red, before the numbers, with what to do about it.
*/

export function IdentityLine({ id, fileName, className }: { id: Identity; fileName: string; className?: string }) {
  const low = id.confidence === 'low';
  const witnesses = id.witnesses.length ? id.witnesses.join(', ') : 'nothing in particular';
  return (
    <p className={`max-w-[62ch] font-sans text-sm leading-relaxed ${low ? 'text-destructive' : 'text-slate'} ${className ?? ''}`}>
      <span className={low ? undefined : 'text-navy'}>Read as {id.object}</span>
      {' '}with {id.confidence} confidence, from {witnesses}
      {id.witnesses.includes('filename') ? '' : ` (“${fileName}” was not a witness)`}.
      {id.reason ? ` ${id.reason}` : ''}
      {low ? ' Confirm the object before trusting the band or the price.' : ''}
    </p>
  );
}

/* The designer's own previews out of a 3MF: the plate as it prints and the top view. Empty for STL and OBJ. */
export function SourceThumbnails({ thumbnails, className }: { thumbnails: string[]; className?: string }) {
  if (thumbnails.length === 0) return null;
  return (
    <div className={className}>
      <div className="flex gap-2">
        {thumbnails.map((src) => (
          <img key={src} src={src} alt="" className="size-28 shrink-0 rounded-lg border border-mist bg-white" />
        ))}
      </div>
      <p className="mt-2 font-sans text-xs text-slate/80">The designer's own previews from the 3MF: the plate as it prints, and the top view.</p>
    </div>
  );
}
