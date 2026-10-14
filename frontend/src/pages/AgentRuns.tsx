import { Link, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { listJobs, listRuns, listUnfinished, resumeRuns, type Job, type JobStatus, type Run } from '@/api/jobs';
import { ConsoleShell, Notice, PageHeader, Panel } from '@/components/site/ConsoleShell';
import { JobRow } from '@/components/site/JobRow';
import { Spinner } from '@/components/site/Spinner';
import { Button } from '@/components/ui/button';
import { formatWhen } from '@/lib/format';
import { cn } from '@/lib/utils';

/*
  Everything the agent has done. Two views of the same work: the models it is
  carrying through intake, and the director processes doing the carrying. The
  filter is the summary: each filter carries its count, and pressing it is the list.
*/

const apiUrl = import.meta.env.VITE_API_URL ?? '/api';

/** Read left to right: what is waiting on a human first, then what is moving. */
const FILTERS: { status: JobStatus; label: string }[] = [
  { status: 'blocked', label: 'Need you' },
  { status: 'running', label: 'Running' },
  { status: 'queued', label: 'Queued' },
  { status: 'done', label: 'Done' },
  { status: 'failed', label: 'Failed' },
];

const segment = (on: boolean) =>
  cn(
    'inline-flex h-8 shrink-0 cursor-pointer items-center gap-2 rounded-lg px-3 font-sans text-[0.8125rem] transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ice',
    on ? 'bg-mist font-medium text-navy' : 'text-slate hover:text-navy',
  );

function Filters({ jobs, active, onPick }: { jobs: Job[]; active: JobStatus | null; onPick: (s: JobStatus | null) => void }) {
  return (
    <div className="-mx-3 flex gap-1 overflow-x-auto overflow-y-hidden" role="group" aria-label="Filter by status">
      <button type="button" className={segment(active == null)} aria-pressed={active == null} onClick={() => onPick(null)}>
        All <span className="tabular-nums opacity-60">{jobs.length}</span>
      </button>
      {FILTERS.map(({ status, label }) => {
        const count = jobs.filter((j) => j.status === status).length;
        if (count === 0 && active !== status) return null;
        return (
          <button key={status} type="button" className={segment(active === status)} aria-pressed={active === status} onClick={() => onPick(status)}>
            {label} <span className="tabular-nums opacity-60">{count}</span>
          </button>
        );
      })}
    </div>
  );
}

/* One director per upload or resume, each with its ledger: is it alive, what did it cost. */
function RunRow({ r }: { r: Run }) {
  return (
    <li className="grid grid-cols-[1fr_auto] items-center gap-x-5 gap-y-1 border-b border-mist px-4 py-3 font-sans text-sm last:border-b-0 sm:grid-cols-[minmax(0,1fr)_9rem_4.5rem_4.5rem] sm:px-5">
      <span className="min-w-0">
        <span className="block truncate font-medium text-navy">{r.files.filter(Boolean).join(', ') || `${r.items.length} items`}</span>
        <span className="block truncate text-[0.8125rem] text-slate">
          {r.run}
          {r.started ? ` · ${formatWhen(r.started)}` : ''}
        </span>
      </span>
      <span className="hidden text-[0.8125rem] tabular-nums text-slate sm:block">
        {r.counts.done} done
        {r.counts.blocked ? ` · ${r.counts.blocked} need you` : ''}
        {r.counts.failed ? ` · ${r.counts.failed} failed` : ''}
      </span>
      <span className="hidden text-right tabular-nums text-slate sm:block">{r.cost?.usd != null ? `$${r.cost.usd.toFixed(2)}` : ''}</span>
      <span className="text-right">
        {r.alive ? (
          <span className="inline-flex items-center gap-1.5 text-navy"><span className="size-1.5 animate-pulse rounded-full bg-navy" aria-hidden="true" />Live</span>
        ) : r.report ? (
          <a href={r.report} target="_blank" rel="noreferrer" className="text-navy underline underline-offset-4">Report</a>
        ) : (
          <span className="text-slate/60">Ended</span>
        )}
      </span>
    </li>
  );
}

export default function AgentRuns() {
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const view = params.get('view') === 'runs' ? 'runs' : 'models';
  const asked = params.get('status') as JobStatus | null;
  const active = asked && FILTERS.some((f) => f.status === asked) ? asked : null;

  const jobs = useQuery({
    queryKey: ['jobs'],
    queryFn: listJobs,
    // Stop once it is clear nothing is listening, instead of hammering a dead
    // host every few seconds for the rest of the session.
    refetchInterval: (query) => (query.state.error ? false : 5000),
  });
  const runs = useQuery({ queryKey: ['runs'], queryFn: listRuns, refetchInterval: 5000 });
  const unfinished = useQuery({ queryKey: ['runs', 'unfinished'], queryFn: listUnfinished, refetchInterval: 15000 });
  const resume = useMutation({
    mutationFn: resumeRuns,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['runs'] });
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
  const pending = unfinished.data?.count ?? 0;
  const shown = jobs.data?.filter((j) => active == null || j.status === active) ?? [];

  const set = (next: Record<string, string | null>) => {
    const merged = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) {
      if (v == null) merged.delete(k);
      else merged.set(k, v);
    }
    setParams(merged, { replace: true });
  };

  return (
    <ConsoleShell>
      <PageHeader
        title="Agent runs"
        action={
          pending > 0 ? (
            <Button variant="outline" disabled={resume.isPending} onClick={() => resume.mutate()}>
              {resume.isPending ? <Spinner className="size-4" /> : null}
              Resume {pending} unfinished
            </Button>
          ) : null
        }
      />

      <div className="mt-6 flex gap-6 border-b border-mist font-sans text-sm" role="tablist">
        {(
          [
            ['models', 'Models', jobs.data?.length],
            ['runs', 'Directors', runs.data?.length],
          ] as const
        ).map(([id, label, n]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={view === id}
            onClick={() => set({ view: id === 'models' ? null : id })}
            className={cn(
              '-mb-px cursor-pointer border-b-2 py-3 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ice',
              view === id ? 'border-navy font-medium text-navy' : 'border-transparent text-slate hover:text-navy',
            )}
          >
            {label}
            {n != null ? <span className="ml-2 tabular-nums text-slate/60">{n}</span> : null}
          </button>
        ))}
      </div>

      {view === 'models' ? (
        <>
          {jobs.data && jobs.data.length > 0 ? (
            <div className="mt-5">
              <Filters jobs={jobs.data} active={active} onPick={(s) => set({ status: s })} />
            </div>
          ) : null}
          <Panel className="mt-4 overflow-hidden">
            {jobs.isPending ? (
              <p className="flex items-center justify-center gap-2 py-12 font-sans text-sm text-slate">
                <Spinner className="size-4" />
                Loading runs.
              </p>
            ) : jobs.isError ? (
              <Notice title={`Can't reach the job API at ${apiUrl}`} tone="bad">
                {jobs.error instanceof Error ? jobs.error.message : 'The request failed.'} Start it with scripts/dev/up.sh.
                <button type="button" onClick={() => jobs.refetch()} className="mt-3 block w-full text-navy underline underline-offset-4">
                  Try again
                </button>
              </Notice>
            ) : jobs.data.length === 0 ? (
              <Notice title="Nothing has run yet">
                <Link to="/upload" className="text-navy underline underline-offset-4">Upload a model</Link> and the agent starts on it.
              </Notice>
            ) : shown.length === 0 ? (
              <Notice title="Nothing with that status right now" />
            ) : (
              shown.map((job) => <JobRow key={job.id} job={job} />)
            )}
          </Panel>
        </>
      ) : (
        <Panel className="mt-5 overflow-hidden">
          {runs.isPending ? (
            <p className="flex items-center justify-center gap-2 py-12 font-sans text-sm text-slate">
              <Spinner className="size-4" />
              Loading directors.
            </p>
          ) : !runs.data || runs.data.length === 0 ? (
            <Notice title="No directors have run yet">One starts with every upload and every resume.</Notice>
          ) : (
            <ul>{runs.data.map((r) => <RunRow key={r.run} r={r} />)}</ul>
          )}
        </Panel>
      )}
    </ConsoleShell>
  );
}
