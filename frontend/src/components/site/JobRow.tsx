import { Link } from 'react-router-dom';
import type { Job, JobStage, JobStatus } from '@/api/jobs';
import { STAGES, STATUS_LABEL } from '@/lib/stages';
import { cn } from '@/lib/utils';

/*
  One agent run, as one line. Lives here rather than on a page because the
  agent-runs list and anything that later wants to show a run in context have
  to describe it the same way.
*/

/** One tick per stage. Decorative: the row states stage and status in words. */
export function StageRail({ stage, status }: { stage: JobStage; status: JobStatus }) {
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

export function JobRow({ job }: { job: Job }) {
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
        {/* Always occupies its column, priced or not, so the rail and the
            status word sit at the same x on every row. */}
        <span className="w-[5.5rem] text-right font-sans text-sm tabular-nums text-navy">
          {job.price != null ? `$${job.price.toFixed(2)}` : null}
        </span>
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
