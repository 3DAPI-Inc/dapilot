import { useEffect, useRef, useState } from 'react';
import { loadPreview } from '@/lib/model/load';
import { createViewer, type ModelStats, type Viewer } from '@/lib/model/viewer';
import { Spinner } from '@/components/site/Spinner';
import { cn } from '@/lib/utils';

/*
  The staged file, on screen, before anything is spent on it.

  Two effects, in this order: one owns the WebGL context for as long as the
  panel is mounted, the other owns whichever file is selected. Rebuilding the
  context per file would burn through the browser's small allowance of them.
*/

type State =
  | { kind: 'loading' }
  | { kind: 'geometry'; stats: ModelStats }
  | { kind: 'image'; url: string; note: string }
  | { kind: 'error'; message: string };

function dimensions(size: ModelStats['size']): string {
  return size.toArray().map((n) => n.toFixed(1)).join(' × ');
}

export function ModelViewer({ file }: { file: File }) {
  const mount = useRef<HTMLDivElement>(null);
  const viewer = useRef<Viewer | null>(null);
  const [state, setState] = useState<State>({ kind: 'loading' });
  const [shown, setShown] = useState(file);

  // React's documented way to reset state when a prop changes: adjust during
  // render, so the stale model is never painted under the new file's name.
  if (shown !== file) {
    setShown(file);
    setState({ kind: 'loading' });
  }

  useEffect(() => {
    const created = createViewer(mount.current!);
    viewer.current = created;
    return () => {
      created.dispose();
      viewer.current = null;
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;

    loadPreview(file, controller.signal).then(
      (preview) => {
        // The file can change while a 200 MB STL is still being read. Whatever
        // the parse produced by then is ours to throw away.
        if (controller.signal.aborted) {
          if (preview.kind === 'geometry') preview.geometry.dispose();
          else URL.revokeObjectURL(preview.url);
          return;
        }
        if (preview.kind === 'geometry') {
          setState({ kind: 'geometry', stats: viewer.current!.show(preview.geometry) });
        } else {
          objectUrl = preview.url;
          setState({ kind: 'image', url: preview.url, note: preview.note });
        }
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ kind: 'error', message: error instanceof Error ? error.message : String(error) });
      },
    );

    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [file]);

  return (
    <figure className="m-0">
      <div className="relative aspect-[4/3] w-full overflow-hidden rounded-2xl border border-mist bg-white">
        {/* Kept mounted in every state so the context outlives the file. */}
        <div
          ref={mount}
          className={cn('absolute inset-0', state.kind !== 'geometry' && 'invisible')}
        />

        {state.kind === 'loading' ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3">
            <Spinner />
            <p className="max-w-[28ch] truncate px-6 font-sans text-sm text-slate">
              Reading {file.name}
            </p>
          </div>
        ) : null}

        {state.kind === 'image' ? (
          <img
            src={state.url}
            alt={`Plate render of ${file.name}`}
            className="absolute inset-0 size-full object-contain p-6"
          />
        ) : null}

        {state.kind === 'error' ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 px-8 text-center">
            <p className="font-sans text-[0.9375rem] text-navy">No preview.</p>
            <p className="font-sans text-sm text-slate">{state.message}</p>
          </div>
        ) : null}

        {state.kind === 'geometry' ? (
          <button
            type="button"
            onClick={() => viewer.current?.reset()}
            className="absolute right-3 bottom-3 rounded-lg bg-paper/85 px-2.5 py-1 font-sans text-xs text-slate backdrop-blur-sm transition-colors hover:text-navy focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ice"
          >
            Reset view
          </button>
        ) : null}
      </div>

      <figcaption className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1 font-sans text-sm text-slate">
        {state.kind === 'geometry' ? (
          <>
            <span
              className="tabular-nums text-navy"
              title="An STL carries no units. Millimetres is what the slicer assumes."
            >
              {dimensions(state.stats.size)} mm
            </span>
            <span className="tabular-nums">
              {state.stats.triangles.toLocaleString()} triangles
            </span>
            <span className="ml-auto text-slate/70">drag to orbit · scroll to zoom · right-drag to pan</span>
          </>
        ) : null}
        {state.kind === 'image' ? <span className="max-w-[60ch]">{state.note}</span> : null}
      </figcaption>
    </figure>
  );
}
