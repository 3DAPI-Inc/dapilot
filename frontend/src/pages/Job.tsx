import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft } from 'lucide-react';
import { getJob, getJobEvents, isLive } from '@/api/jobs';
import { BuyBox } from '@/components/product/BuyBox';
import { Gallery } from '@/components/product/Gallery';
import { ActivityPanel, MarketPanel, PricingPanel, ProductionPanel } from '@/components/product/panels';
import { SocialPanel } from '@/components/product/SocialPanel';
import { ConsoleShell, Notice, Panel } from '@/components/site/ConsoleShell';
import { StageSteps } from '@/components/site/JobRow';
import { Spinner } from '@/components/site/Spinner';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

/*
  One model, shown the way a shop shows a product: pictures on the left, the
  name, the price and the one decision on the right. That is everything an
  operator needs to act. Why the price is what it is, what the market looks
  like, what the slicer measured and what the pipeline did are one tab away,
  and the tab is kept in the URL so a link can point at any of them.
*/

const TABS = ['market', 'pricing', 'production', 'social', 'activity'] as const;
type Tab = (typeof TABS)[number];
const apiUrl = import.meta.env.VITE_API_URL ?? '/api';

export default function Job() {
  const { jobId = '' } = useParams();
  const [params, setParams] = useSearchParams();
  const job = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => getJob(jobId),
    refetchInterval: (q) => (isLive(q.state.data) ? 3000 : false),
  });
  const events = useQuery({ queryKey: ['job', jobId, 'events'], queryFn: () => getJobEvents(jobId) });

  const asked = params.get('tab') as Tab | null;
  const tab: Tab = asked && TABS.includes(asked) ? asked : 'market';

  return (
    <ConsoleShell>
      <Link
        to="/agent-runs"
        className="inline-flex items-center gap-1.5 font-sans text-sm text-slate underline-offset-4 transition-colors hover:text-navy hover:underline"
      >
        <ArrowLeft className="size-3.5" aria-hidden="true" />
        Agent runs
      </Link>

      {job.isPending ? (
        <p className="flex items-center gap-2 py-16 font-sans text-sm text-slate">
          <Spinner className="size-4" />
          Loading this model.
        </p>
      ) : job.isError ? (
        <Panel className="mt-6">
          <Notice title="This model did not load" tone="bad">
            {job.error instanceof Error ? job.error.message : 'The request failed.'} It was asked for at {apiUrl}. Either the id is
            wrong or the job API is not running.
            <button type="button" onClick={() => job.refetch()} className="mt-3 block w-full text-navy underline underline-offset-4">
              Try again
            </button>
          </Notice>
        </Panel>
      ) : (
        <>
          <div className="mt-6 grid gap-8 md:grid-cols-2 md:gap-12 lg:grid-cols-[minmax(0,30rem)_minmax(0,1fr)]">
            <Gallery job={job.data} />
            <BuyBox job={job.data} />
          </div>

          <div className="mt-10">
            <StageSteps stage={job.data.stage} status={job.data.status} />
          </div>

          <Panel className="mt-10 overflow-hidden">
            <Tabs value={tab} onValueChange={(next) => setParams(next === 'market' ? {} : { tab: next }, { replace: true })}>
              <TabsList>
                <TabsTrigger value="market">Market</TabsTrigger>
                <TabsTrigger value="pricing">Pricing</TabsTrigger>
                <TabsTrigger value="production">Production</TabsTrigger>
                <TabsTrigger value="social">Social</TabsTrigger>
                <TabsTrigger value="activity">Activity</TabsTrigger>
              </TabsList>
              <TabsContent value="market"><MarketPanel job={job.data} /></TabsContent>
              <TabsContent value="pricing"><PricingPanel job={job.data} /></TabsContent>
              <TabsContent value="production"><ProductionPanel job={job.data} /></TabsContent>
              {/* Mounted only when opened: reading it asks each platform whether we are still signed in. */}
              <TabsContent value="social">{tab === 'social' ? <SocialPanel jobId={jobId} /> : null}</TabsContent>
              <TabsContent value="activity"><ActivityPanel job={job.data} events={events.data ?? []} /></TabsContent>
            </Tabs>
          </Panel>
        </>
      )}
    </ConsoleShell>
  );
}
