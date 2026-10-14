import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Box } from 'lucide-react';
import { draftThumbnail, type Job, type JobStage, type JobStatus } from '@/api/jobs';
import { STAGES, statusPhrase } from '@/lib/stages';
import { cn } from '@/lib/utils';

/*
  One agent run, as one line. Lives here rather than on a page because the
  agent-runs list and anything that later wants to show a run in context have
  to describe it the same way.
*/

const tickTone = (status: JobStatus) =>
  status === 'blocked' ? 'bg-ice' : status === 'failed' ? 'bg-destructive' : 'bg-navy';

/** One tick per stage. Hover names the stage; the row states it in words too. */
export function StageRail({ stage, status }: { stage: JobStage; status: JobStatus }) {
  const current = STAGES.indexOf(stage);
  const filledThrough = status === 'done' ? STAGES.length - 1 : current;
  return (
    <span className="flex items-center gap-1" role="img" aria-label={`Stage ${current + 1} of ${STAGES.length}: ${stage}`}>
      {STAGES.map((name, i) => (
        <span
          key={name}
          title={name}
          className={cn(
            'h-3.5 w-1 rounded-full',
            i > filledThrough && 'bg-mist',
            i < filledThrough && 'bg-navy/40',
            i === filledThrough && tickTone(status),
          )}
        />
      ))}
    </span>
  );
}

/** The same nine stages at full width, named, for the page about one item. */
export function StageSteps({ stage, status }: { stage: JobStage; status: JobStatus }) {
  const current = STAGES.indexOf(stage);
  const filledThrough = status === 'done' ? STAGES.length - 1 : current;
  return (
    <ol className="grid grid-cols-9 gap-1.5" aria-label="Intake progress">
      {STAGES.map((name, i) => (
        <li key={name} aria-current={i === filledThrough ? 'step' : undefined} title={name}>
          <span
            className={cn(
              'block h-1.5 rounded-full',
              i > filledThrough && 'bg-mist',
              i < filledThrough && 'bg-navy/35',
              i === filledThrough && tickTone(status),
              i === filledThrough && status === 'running' && 'animate-pulse',
            )}
          />
          <span
            className={cn(
              'mt-2 hidden truncate font-sans text-[0.6875rem] capitalize sm:block',
              i === filledThrough ? 'font-medium text-navy' : i < filledThrough ? 'text-slate' : 'text-slate/50',
            )}
          >
            {name}
          </span>
        </li>
      ))}
    </ol>
  );
}

const TONE: Record<JobStatus, string> = {
  queued: 'text-slate',
  running: 'text-navy',
  blocked: 'font-medium text-navy',
  done: 'text-ok',
  failed: 'font-medium text-destructive',
};
const DOT: Record<JobStatus, string> = {
  queued: 'bg-slate/30',
  running: 'animate-pulse bg-navy',
  blocked: 'bg-ice',
  done: 'bg-ok',
  failed: 'bg-destructive',
};

/** Status as plain type: a small dot and a phrase. No box around it. */
export function StatusText({ status, stage, className }: { status: JobStatus; stage: JobStage; className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-2 font-sans text-sm whitespace-nowrap', TONE[status], className)}>
      <span className={cn('size-1.5 shrink-0 rounded-full', DOT[status])} aria-hidden="true" />
      {statusPhrase(stage, status)}
    </span>
  );
}

/** The first draft render, or a quiet placeholder until preview has run. */
export function JobThumb({ job, className }: { job: Job; className?: string }) {
  const src = draftThumbnail(job) ?? job.thumbnails?.[0] ?? null;
  const [broken, setBroken] = useState(false);
  return (
    <span className={cn('flex size-11 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-mist bg-mist/40', className)}>
      {src && !broken ? (
        <img src={src} alt="" loading="lazy" onError={() => setBroken(true)} className="size-full object-cover" />
      ) : (
        <Box className="size-4 text-slate/40" aria-hidden="true" />
      )}
    </span>
  );
}

export function JobRow({ job }: { job: Job }) {
  return (
    <Link
      to={`/jobs/${job.id}`}
      className="flex items-center gap-4 border-b border-mist px-4 py-3 transition-colors last:border-b-0 hover:bg-mist/30 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ice sm:px-5"
    >
      <JobThumb job={job} />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-sans text-[0.9375rem] font-medium text-navy">{job.fileName}</span>
        {/* Queued rows say it too: "queued" with nothing running is the case a person most needs to see. */}
        {job.reason ? (
          <span className={cn('mt-0.5 block truncate font-sans text-[0.8125rem] first-letter:uppercase', job.status === 'failed' ? 'text-destructive' : 'text-slate')}>
            {job.reason}
          </span>
        ) : null}
      </span>
      <span className="hidden md:block">
        <StageRail stage={job.stage} status={job.status} />
      </span>
      {/* Always occupies its column, priced or not, so every row lines up. */}
      <span className="hidden w-16 text-right font-sans text-sm tabular-nums text-slate sm:block" title="dapi quote for one unit">
        {job.price != null ? `$${job.price.toFixed(2)}` : ''}
      </span>
      <StatusText status={job.status} stage={job.stage} className="w-[10.5rem] justify-end text-[0.8125rem]" />
    </Link>
  );
}
