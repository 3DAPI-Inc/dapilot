import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { apiUrl } from '@/api/dapi';
import { listJobs, type Job, type JobStatus } from '@/api/jobs';
import { JobRow } from '@/components/site/JobRow';
import { ConsoleNav, SiteHeader } from '@/components/site/SiteHeader';
import { Spinner } from '@/components/site/Spinner';
import { STAGES } from '@/lib/stages';

/*
  Everything the agent has done, in one list.

  This used to be the bottom half of the upload page, which made it the thing
  you saw while doing something else. A run outlives the upload that started
  it, so it gets its own room.
*/

/** Read left to right: what is waiting on a human first, then what is moving. */
const SUMMARY: { status: JobStatus; label: string }[] = [
  { status: 'blocked', label: 'need you' },
  { status: 'running', label: 'running' },
  { status: 'queued', label: 'queued' },
  { status: 'done', label: 'done' },
  { status: 'failed', label: 'failed' },
];

function Summary({ jobs }: { jobs: Job[] }) {
  const counts = SUMMARY.map((entry) => ({
    ...entry,
    count: jobs.filter((job) => job.status === entry.status).length,
  })).filter((entry) => entry.count > 0);

  if (counts.length === 0) return null;

  return (
    <dl className="mt-8 flex flex-wrap gap-x-10 gap-y-4">
      {counts.map(({ status, label, count }) => (
        <div key={status}>
          <dt className="font-sans text-[0.6875rem] uppercase tracking-[0.22em] text-slate/60">
            {label}
          </dt>
          <dd className="mt-1 font-display text-2xl font-semibold tabular-nums tracking-tight">
            {count}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export default function AgentRuns() {
  const jobs = useQuery({
    queryKey: ['jobs'],
    queryFn: listJobs,
    // Stop once it is clear nothing is listening, instead of hammering a dead
    // host every few seconds for the rest of the session.
    refetchInterval: (query) => (query.state.error ? false : 5000),
  });

  return (
    <div className="min-h-svh bg-paper text-navy">
      <SiteHeader>
        <ConsoleNav />
      </SiteHeader>

      <main className="mx-auto max-w-[940px] px-5 pb-24 sm:px-10">
        <h1 className="font-display text-4xl font-semibold tracking-tight">Agent runs</h1>
        <p className="mt-3 max-w-[52ch] font-sans leading-relaxed text-slate">
          One row per model the agent is working through. The rail is the eight stages of
          intake; the row says which one it is on and what it is waiting for. Open a row for
          its stage-by-stage record.
        </p>

        {jobs.data ? <Summary jobs={jobs.data} /> : null}

        <div className="mt-10 border-t border-mist">
          {jobs.isPending ? (
            <p className="flex items-center gap-2 py-8 font-sans text-sm text-slate">
              <Spinner className="size-4" />
              Loading runs.
            </p>
          ) : jobs.isError ? (
            <div className="py-8">
              <p className="font-sans text-[0.9375rem] text-navy">
                Can't reach the dapi API at {apiUrl}.
              </p>
              <p className="mt-1 max-w-[52ch] font-sans text-sm leading-relaxed text-slate">
                {jobs.error instanceof Error ? jobs.error.message : 'The request failed.'} Check
                VITE_DAPI_API_URL and VITE_DAPI_API_KEY in frontend/.env.
              </p>
              <button
                type="button"
                onClick={() => jobs.refetch()}
                className="mt-4 font-sans text-sm text-navy underline underline-offset-4"
              >
                Try again
              </button>
            </div>
          ) : jobs.data.length === 0 ? (
            <p className="py-8 font-sans text-[0.9375rem] text-slate">
              Nothing has run yet.{' '}
              <Link to="/upload" className="text-navy underline underline-offset-4">
                Upload a model
              </Link>{' '}
              and it starts one.
            </p>
          ) : (
            jobs.data.map((job) => <JobRow key={job.id} job={job} />)
          )}
        </div>

        <h2 className="mt-16 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.22em] text-slate/60">
          The rail
        </h2>
        <p className="mt-3 max-w-[52ch] font-sans text-sm leading-relaxed text-slate">
          Eight ticks, one per stage, in order: {STAGES.join(' · ')}. Filled ticks are behind
          the run, the solid one is where it stands. It turns ice when a gate is waiting on a
          person and red when the stage failed.
        </p>
      </main>
    </div>
  );
}
