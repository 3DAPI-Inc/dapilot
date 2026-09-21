import { Suspense, lazy, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ACCEPTED_EXTENSIONS, type Job, createJobs, getJob, isLive } from '@/api/jobs';
import { UploadCloud } from 'lucide-react';
import { ConsoleShell, PageHeader, Panel } from '@/components/site/ConsoleShell';
import { JobThumb, StatusText } from '@/components/site/JobRow';
import { Spinner } from '@/components/site/Spinner';
import { extensionOf } from '@/lib/model/load';
import { cn } from '@/lib/utils';

// three and its loaders are the biggest thing the console imports, and only
// this page ever needs them. Staging a file is what pays for them.
const ModelViewer = lazy(() =>
  import('@/components/model/ModelViewer').then((m) => ({ default: m.ModelViewer })),
);

/** The viewer's own frame, so the layout does not jump when the chunk lands. */
function ViewerFallback() {
  return (
    <div className="flex aspect-[6/5] w-full items-center justify-center rounded-2xl border border-mist bg-white">
      <Spinner />
    </div>
  );
}

/*
  The console's front door.

  Picking a file no longer starts a run. Files are staged, looked at, and
  submitted deliberately, because the first thing an upload does is spend a
  slicer run on dapi. What the run then did is on /agent-runs; this page ends
  the moment the work is handed over.
*/

interface Staged {
  id: string;
  file: File;
}

type Rejection = { id: string; name: string; reason: string };

/**
 * One run this page started, followed while the pipeline moves it. The row
 * says the latest fact the work dir holds: the dapi price and weight once
 * sliced, the market verdict once researched, the proposed sale price once
 * priced, and why it stopped when it stopped.
 */
function StartedRow({ job }: { job: Job }) {
  const live = useQuery({
    queryKey: ['job', job.id],
    queryFn: () => getJob(job.id),
    initialData: job,
    refetchInterval: (q) => (isLive(q.state.data) ? 3000 : false),
  });
  const j = live.data;
  const m = j.metrics;
  const facts: string[] = [];
  if (j.price != null) facts.push(`$${j.price.toFixed(2)} dapi`);
  if (m) facts.push(`${m.model_g.toFixed(1)} g`);
  if (j.viability?.verdict) facts.push(j.viability.verdict.replace('_', ' '));
  if (j.pricing?.proposal) facts.push(`sell at $${j.pricing.proposal.price.toFixed(2)}`);
  return (
    <li className="border-b border-mist last:border-b-0">
      <Link to={`/jobs/${j.id}`} className="flex items-center gap-4 px-4 py-3 transition-colors hover:bg-mist/30 sm:px-5">
        <JobThumb job={j} />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-sans text-[0.9375rem] font-medium text-navy">{j.fileName}</span>
          <span className={cn('mt-0.5 block truncate font-sans text-[0.8125rem] tabular-nums', j.status === 'failed' ? 'text-destructive' : 'text-slate')}>
            {j.status === 'failed' || j.status === 'blocked' ? j.reason : facts.join(' · ') || 'Just started'}
          </span>
        </span>
        <StatusText status={j.status} stage={j.stage} className="text-[0.8125rem]" />
      </Link>
    </li>
  );
}

function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

export default function Upload() {
  const queryClient = useQueryClient();
  const [dragging, setDragging] = useState(false);
  const [staged, setStaged] = useState<Staged[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [rejections, setRejections] = useState<Rejection[]>([]);
  const [steps, setSteps] = useState<Record<number, string>>({});
  const [sent, setSent] = useState(0);
  const [started, setStarted] = useState<Job[]>([]);

  // dragenter/dragleave fire for every child too, so depth is counted rather
  // than toggled - otherwise crossing the inner text clears the drag state.
  const dragDepth = useRef(0);
  // Date.now() collides when several files are staged in the same tick.
  const nextId = useRef(0);

  // Derived, not stored: removing the staged file the panel was showing falls
  // back to the first one without a second render to correct the selection.
  const selected = staged.find((s) => s.id === selectedId) ?? staged[0] ?? null;

  const upload = useMutation({
    mutationFn: (files: File[]) =>
      createJobs(files, (index, step) => setSteps((prev) => ({ ...prev, [index]: step }))),
    onSuccess: ({ jobs, failures }) => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
      setRejections((prev) => [
        ...prev,
        ...failures.map((f) => ({ id: `${f.fileName}-${nextId.current++}`, name: f.fileName, reason: f.reason })),
      ]);
      // Only the files that made it leave the tray, so a retry is one click.
      const failed = new Set(failures.map((f) => f.fileName));
      setStaged((prev) => prev.filter((s) => failed.has(s.file.name)));
      setSent(jobs.length);
      setStarted((prev) => [...jobs, ...prev.filter((p) => !jobs.some((j) => j.id === p.id))]);
    },
    onSettled: () => setSteps({}),
  });

  function accept(list: FileList | null) {
    if (!list?.length) return;
    const add: Staged[] = [];
    const bad: Rejection[] = [];

    for (const file of Array.from(list)) {
      const ext = extensionOf(file.name);
      // Same word the analyze-model skill returns, so the UI and the pipeline
      // describe the same failure identically.
      if (!ACCEPTED_EXTENSIONS.includes(ext as (typeof ACCEPTED_EXTENSIONS)[number])) {
        bad.push({
          id: `${file.name}-${nextId.current++}`,
          reason: `unsupported: ${ext || 'no extension'}`,
          name: file.name,
        });
      } else {
        add.push({ id: `${file.name}-${nextId.current++}`, file });
      }
    }

    if (bad.length) setRejections((prev) => [...prev, ...bad]);
    if (add.length) {
      setSent(0);
      setStaged((prev) => [...prev, ...add]);
      setSelectedId(add[0].id);
    }
  }

  const busy = upload.isPending;

  return (
    <ConsoleShell>
        <PageHeader title="Upload" note="Stage your model files, look them over, then hand them to the agent." />

        <div
          onDragEnter={(e) => {
            e.preventDefault();
            dragDepth.current += 1;
            setDragging(true);
          }}
          onDragOver={(e) => e.preventDefault()}
          onDragLeave={(e) => {
            e.preventDefault();
            dragDepth.current -= 1;
            if (dragDepth.current <= 0) setDragging(false);
          }}
          onDrop={(e) => {
            e.preventDefault();
            dragDepth.current = 0;
            setDragging(false);
            accept(e.dataTransfer.files);
          }}
          className="mt-6"
        >
          <label
            className={cn(
              'block cursor-pointer rounded-2xl border border-dashed text-center transition-colors',
              'focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-ice',
              // The box gives up its height once there is something to look at.
              staged.length > 0 ? 'px-8 py-8' : 'px-8 py-16',
              dragging ? 'border-ice bg-ice/10' : 'border-slate/25 bg-white hover:border-slate/50',
            )}
          >
            <input
              type="file"
              multiple
              accept={ACCEPTED_EXTENSIONS.join(',')}
              className="sr-only"
              onChange={(e) => {
                accept(e.target.files);
                // Let the same file be picked twice in a row.
                e.target.value = '';
              }}
            />
            {staged.length > 0 ? (
              <span className="block font-sans text-[0.9375rem] text-slate">
                Drop more model files, or <span className="text-navy underline underline-offset-4">choose files</span>
              </span>
            ) : (
              <>
                <UploadCloud className="mx-auto size-7 text-slate/50" aria-hidden="true" />
                <span className="mt-4 block font-display text-xl font-semibold tracking-tight">
                  Drop model files here
                </span>
                <span className="mt-1.5 block font-sans text-sm text-slate">
                  or <span className="text-navy underline underline-offset-4">choose files</span>. STL, 3MF or OBJ. Nothing is sent until you press Upload.
                </span>
              </>
            )}
          </label>
        </div>

        {started.length > 0 ? (
          <section className="mt-8">
            <div className="flex items-baseline justify-between gap-4">
              <h2 className="font-display text-[0.9375rem] font-semibold tracking-tight">
                {sent > 0 ? (sent === 1 ? 'One run started' : `${sent} runs started`) : 'Started'}
              </h2>
              <Link to="/agent-runs" className="font-sans text-sm text-slate underline-offset-4 hover:text-navy hover:underline">
                All agent runs
              </Link>
            </div>
            <Panel className="mt-3 overflow-hidden">
              <ul>
                {started.map((job) => (
                  <StartedRow key={job.id} job={job} />
                ))}
              </ul>
            </Panel>
          </section>
        ) : null}

        {staged.length > 0 ? (
          <section className="mt-8 grid gap-6 md:grid-cols-[minmax(0,1fr)_18rem]">
            {/* Keyed on the file so switching selection remounts the viewer's
                loading state rather than showing the last model under a new name. */}
            {selected ? (
              <Suspense fallback={<ViewerFallback />}>
                <ModelViewer key={selected.id} file={selected.file} />
              </Suspense>
            ) : null}

            <Panel className="flex flex-col self-start p-5">
              <h2 className="font-display text-[0.9375rem] font-semibold tracking-tight">
                Staged <span className="ml-1 font-sans text-sm font-normal tabular-nums text-slate">{staged.length}</span>
              </h2>

              <ul className="mt-2 border-t border-mist">
                {staged.map((entry, index) => (
                  <li key={entry.id} className="border-b border-mist">
                    <div className="flex items-center gap-2 py-2.5">
                      <button
                        type="button"
                        onClick={() => setSelectedId(entry.id)}
                        aria-current={entry.id === selected?.id}
                        className="min-w-0 flex-1 text-left"
                      >
                        <span
                          className={cn(
                            'block truncate font-sans text-sm',
                            entry.id === selected?.id ? 'text-navy' : 'text-slate hover:text-navy',
                          )}
                        >
                          {entry.file.name}
                        </span>
                        <span className="mt-0.5 flex items-center gap-1.5 font-sans text-xs text-slate/70">
                          {busy && steps[index] ? (
                            <>
                              <Spinner className="size-3 border" />
                              {steps[index]}
                            </>
                          ) : (
                            fileSize(entry.file.size)
                          )}
                        </span>
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => setStaged((prev) => prev.filter((s) => s.id !== entry.id))}
                        aria-label={`Remove ${entry.file.name}`}
                        className="shrink-0 rounded px-1 font-sans text-xs text-slate/60 transition-colors hover:text-destructive focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ice disabled:opacity-40"
                      >
                        Remove
                      </button>
                    </div>
                  </li>
                ))}
              </ul>

              <button
                type="button"
                disabled={busy}
                onClick={() => upload.mutate(staged.map((s) => s.file))}
                className="mt-6 inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-navy px-5 font-display text-[0.9375rem] font-semibold tracking-tight text-paper transition-transform hover:-translate-y-0.5 focus-visible:outline-2 focus-visible:outline-offset-3 focus-visible:outline-ice disabled:translate-y-0 disabled:opacity-60 motion-reduce:hover:translate-y-0"
              >
                {busy ? (
                  <>
                    <Spinner className="size-4 border-paper/30 border-t-paper" />
                    Uploading
                  </>
                ) : staged.length === 1 ? (
                  'Upload file'
                ) : (
                  `Upload ${staged.length} files`
                )}
              </button>

              <p className="mt-3 font-sans text-xs leading-relaxed text-slate">
                Each file is sliced and priced on dapi, then researched. Nothing is published.
              </p>
            </Panel>
          </section>
        ) : null}

        {rejections.length > 0 ? (
          <ul className="mt-8 space-y-px overflow-hidden rounded-xl">
            {rejections.map((rejection) => (
              <li
                key={rejection.id}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 bg-mist/60 px-4 py-3 font-sans text-sm"
              >
                <span className="font-medium text-navy">{rejection.name}</span>
                <span className="text-destructive">{rejection.reason}</span>
                <button
                  type="button"
                  onClick={() =>
                    setRejections((prev) => prev.filter((r) => r.id !== rejection.id))
                  }
                  className="ml-auto text-slate underline underline-offset-4 hover:text-navy"
                >
                  Dismiss
                </button>
              </li>
            ))}
          </ul>
        ) : null}
    </ConsoleShell>
  );
}
