import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ACCEPTED_EXTENSIONS,
  createJobs,
  listJobs,
  type Job,
  type JobStage,
  type JobStatus,
} from '@/api/jobs';
import { SiteHeader, headerLink } from '@/components/site/SiteHeader';
import { cn } from '@/lib/utils';

/*
  The console's front door: get model files in, and show what happened to the
  ones already here. Same restraint as the landing - one input, one list, and
  nothing else competing for attention.
*/

const STAGES: JobStage[] = [
  'intake', 'analyze', 'slice', 'price', 'render', 'research', 'copy', 'publish',
];

/**
 * Said from the operator's side, not the system's. `blocked` is the pipeline's
 * word for "a gate is waiting on a human", so the row says what the human has
 * to do about it.
 */
const STATUS_LABEL: Record<JobStatus, string> = {
  queued: 'queued',
  running: 'running',
  blocked: 'needs you',
  done: 'done',
  failed: 'failed',
};

const API_BASE = import.meta.env.VITE_API_URL ?? '/api';

/** One tick per stage. Decorative: the row states stage and status in words. */
function StageRail({ stage, status }: { stage: JobStage; status: JobStatus }) {
  const current = STAGES.indexOf(stage);
  const filledThrough = status === 'done' ? STAGES.length - 1 : current;
  return (
    <span className="flex items-center gap-1" aria-hidden="true">
      {STAGES.map((name, i) => (
        <span
          key={name}
          className={cn(
            'h-3.5 w-1 rounded-full',
            i > filledThrough && 'bg-mist',
            i < filledThrough && 'bg-navy/40',
            i === filledThrough &&
              (status === 'blocked'
                ? 'bg-ice'
                : status === 'failed'
                  ? 'bg-destructive'
                  : 'bg-navy'),
          )}
        />
      ))}
    </span>
  );
}

function JobRow({ job }: { job: Job }) {
  return (
    <Link
      to={`/jobs/${job.id}`}
      className="flex flex-col gap-2 border-b border-mist py-4 transition-colors hover:bg-mist/40 sm:flex-row sm:items-center sm:gap-5"
    >
      <span className="min-w-0 flex-1">
        <span className="block truncate font-sans text-[0.9375rem] text-navy">{job.fileName}</span>
        {job.reason ? (
          <span
            className={cn(
              'mt-0.5 block truncate font-sans text-sm',
              job.status === 'failed' ? 'text-destructive' : 'text-slate',
            )}
          >
            {job.reason}
          </span>
        ) : null}
      </span>
      <span className="flex items-center gap-3">
        <StageRail stage={job.stage} status={job.status} />
        <span
          className={cn(
            'w-[8.5rem] font-sans text-sm',
            job.status === 'blocked' && 'text-navy',
            job.status === 'failed' && 'text-destructive',
            job.status !== 'blocked' && job.status !== 'failed' && 'text-slate',
          )}
        >
          {job.stage} · {STATUS_LABEL[job.status]}
        </span>
      </span>
    </Link>
  );
}

type Rejection = { id: string; name: string; reason: string };

export default function Upload() {
  const queryClient = useQueryClient();
  const [dragging, setDragging] = useState(false);
  const [rejections, setRejections] = useState<Rejection[]>([]);
  const [uploading, setUploading] = useState<string[]>([]);
  // dragenter/dragleave fire for every child too, so depth is counted rather
  // than toggled - otherwise crossing the inner text clears the drag state.
  const dragDepth = useRef(0);

  const jobs = useQuery({
    queryKey: ['jobs'],
    queryFn: listJobs,
    // Stop once it is clear nothing is listening, instead of hammering a dead
    // port every few seconds for the rest of the session.
    refetchInterval: (query) => (query.state.error ? false : 5000),
  });

  const upload = useMutation({
    mutationFn: createJobs,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
    onError: (error: Error, files) => {
      setRejections((prev) => [
        ...prev,
        ...files.map((file) => ({
          id: `${file.name}-${Date.now()}`,
          name: file.name,
          reason: error.message || 'upload failed',
        })),
      ]);
    },
    onSettled: () => setUploading([]),
  });

  function accept(list: FileList | null) {
    if (!list?.length) return;
    const files: File[] = [];
    const bad: Rejection[] = [];

    for (const file of Array.from(list)) {
      const dot = file.name.lastIndexOf('.');
      const ext = dot === -1 ? '' : file.name.slice(dot).toLowerCase();
      // Same word the analyze-model skill returns, so the UI and the pipeline
      // describe the same failure identically.
      if (!ACCEPTED_EXTENSIONS.includes(ext as (typeof ACCEPTED_EXTENSIONS)[number])) {
        bad.push({
          id: `${file.name}-${Date.now()}`,
          name: file.name,
          reason: `unsupported: ${ext || 'no extension'}`,
        });
      } else {
        files.push(file);
      }
    }

    if (bad.length) setRejections((prev) => [...prev, ...bad]);
    if (files.length) {
      setUploading(files.map((f) => f.name));
      upload.mutate(files);
    }
  }

  return (
    <div className="min-h-svh bg-paper text-navy">
      <SiteHeader>
        <Link to="/catalog" className={headerLink}>
          Catalogue
        </Link>
      </SiteHeader>

      <main className="mx-auto max-w-[940px] px-5 pb-24 sm:px-10">
        <h1 className="font-display text-4xl font-semibold tracking-tight">Upload</h1>

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
          className="mt-8"
        >
          <label
            className={cn(
              'block cursor-pointer rounded-2xl border px-8 py-16 text-center transition-colors',
              'focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-ice',
              dragging ? 'border-ice bg-ice/10' : 'border-mist bg-white hover:border-slate/25',
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
            <span className="block font-display text-2xl font-semibold tracking-tight">
              Drop model files here
            </span>
            <span className="mt-2 block font-sans text-slate underline underline-offset-4">
              or choose files
            </span>
            <span className="mt-7 block font-sans text-[0.6875rem] uppercase tracking-[0.22em] text-slate/50">
              stl · 3mf · obj
            </span>
          </label>
        </div>

        <p className="mt-5 max-w-[46ch] font-sans text-sm leading-relaxed text-slate">
          Files are keyed by content, so the same model uploaded twice under two names is
          processed once.
        </p>

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

        <section className="mt-16">
          <h2 className="font-display text-[0.6875rem] font-semibold uppercase tracking-[0.22em] text-slate/60">
            Jobs
          </h2>

          <div className="mt-4 border-t border-mist">
            {uploading.map((name) => (
              <div
                key={name}
                className="flex items-center justify-between gap-5 border-b border-mist py-4 font-sans text-[0.9375rem]"
              >
                <span className="min-w-0 flex-1 truncate text-navy">{name}</span>
                <span className="text-sm text-slate">uploading</span>
              </div>
            ))}

            {jobs.isPending ? (
              <p className="py-8 font-sans text-sm text-slate">Loading jobs.</p>
            ) : jobs.isError ? (
              <div className="py-8">
                <p className="font-sans text-[0.9375rem] text-navy">
                  Can't reach the job API at {API_BASE}.
                </p>
                <p className="mt-1 font-sans text-sm text-slate">
                  Start it and this list fills in. Uploads will fail until it answers.
                </p>
                <button
                  type="button"
                  onClick={() => jobs.refetch()}
                  className="mt-4 font-sans text-sm text-navy underline underline-offset-4"
                >
                  Try again
                </button>
              </div>
            ) : jobs.data.length === 0 && uploading.length === 0 ? (
              <p className="py-8 font-sans text-[0.9375rem] text-slate">
                No jobs yet. Drop a model file above and it starts one.
              </p>
            ) : (
              jobs.data.map((job) => <JobRow key={job.id} job={job} />)
            )}
          </div>
        </section>
      </main>
    </div>
  );
}
