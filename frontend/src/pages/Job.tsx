import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getJob, getJobEvents } from '@/api/jobs';
import { ConsoleNav, SiteHeader } from '@/components/site/SiteHeader';

/* One job, read straight off its work dir through the job API. */
export default function Job() {
  const { jobId = '' } = useParams();
  const job = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => getJob(jobId),
    refetchInterval: (q) => (q.state.data?.status === 'running' || q.state.data?.status === 'queued' ? 3000 : false),
  });
  const events = useQuery({ queryKey: ['job', jobId, 'events'], queryFn: () => getJobEvents(jobId) });

  return (
    <div className="min-h-svh bg-paper text-navy">
      <SiteHeader>
        <ConsoleNav />
      </SiteHeader>
      <main className="mx-auto max-w-[940px] px-5 pb-24 sm:px-10">
        <Link
          to="/agent-runs"
          className="font-sans text-sm text-slate underline-offset-4 transition-colors hover:text-navy hover:underline"
        >
          Agent runs
        </Link>

        {job.isPending ? (
          <p className="py-8 font-sans text-sm text-slate">Loading.</p>
        ) : job.isError ? (
          <p className="py-8 font-sans text-sm text-destructive">No such job.</p>
        ) : (
          <>
            <h1 className="font-display text-3xl font-semibold tracking-tight">{job.data.fileName}</h1>
            <p className="mt-1 font-sans text-sm text-slate">
              {job.data.stage} · {job.data.status}
              {job.data.reason ? ` · ${job.data.reason}` : ''}
            </p>

            <dl className="mt-8 grid grid-cols-2 gap-x-8 gap-y-3 font-sans text-sm sm:grid-cols-3">
              {job.data.price != null ? (
                <>
                  <dt className="text-slate">unit price</dt>
                  <dd className="col-span-1 tabular-nums sm:col-span-2">${job.data.price.toFixed(2)}</dd>
                </>
              ) : null}
              {job.data.quoteId ? (
                <>
                  <dt className="text-slate">dapi quote</dt>
                  <dd className="sm:col-span-2">{job.data.quoteId}</dd>
                </>
              ) : null}
              {job.data.batch?.prices?.length ? (
                <>
                  <dt className="text-slate">batch</dt>
                  <dd className="sm:col-span-2">
                    {job.data.batch.prices.map((p) => `${p.quantity} × $${p.unit.toFixed(2)}`).join(' · ')}
                  </dd>
                </>
              ) : null}
            </dl>

            <h2 className="mt-12 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.22em] text-slate/60">
              Events
            </h2>
            <ul className="mt-3 border-t border-mist font-sans text-sm">
              {(events.data ?? []).map((e, i) => (
                <li key={i} className="flex gap-4 border-b border-mist py-2">
                  <span className="w-20 text-slate">{e.stage}</span>
                  <span className="w-20">{e.status}</span>
                  <span className="tabular-nums text-slate">{(e.durationMs / 1000).toFixed(1)} s</span>
                  <span className="ml-auto text-slate">{e.at}</span>
                </li>
              ))}
              {events.data && events.data.length === 0 ? (
                <li className="py-2 text-slate">Nothing has run yet.</li>
              ) : null}
            </ul>
          </>
        )}
      </main>
    </div>
  );
}
