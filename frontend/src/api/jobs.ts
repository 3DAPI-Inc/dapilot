import { api } from './client';
import type { Quote, QuoteMetrics } from './dapi';

/*
  Jobs come from dapilot's own job API on :3100, which reads them off the
  work dirs the pipeline writes. The browser never calls dapi: since
  2026-09-09 the upload goes to the server, which strips foreign 3mf project
  settings, files the model under work/<sha256>/ and starts a run whose
  workers are Claude agents. This module is the only place that knows the
  server's routes.
*/

// Mirrors plugins/dapilot/skills/run-intake/reference/stages.json. Keep in sync.
export type JobStage =
  | 'intake' | 'analyze' | 'slice' | 'preview'
  | 'research' | 'price' | 'design' | 'copy' | 'publish';

export type JobStatus = 'queued' | 'running' | 'blocked' | 'done' | 'failed';

export interface MarketBand {
  n: number;
  min: number;
  p25: number;
  median: number;
  p75: number;
  max: number;
  currency?: string;
}

/** What the design stage made: design.json, with file paths turned into URLs. */
export interface Design {
  name: string;
  color: string;
  reason: string;
  photos: { id: string; kind: 'studio' | 'lifestyle'; url: string; px: [number | null, number | null] }[];
  video: { id: 'lifestyle_video' | 'turntable'; url: string; seconds: number | null } | null;
  turntable: string | null;
  rejected: { id: string; reason: string }[];
  failed: { id: string; reason: string | null }[];
  spendUsd: number;
  warnings: string[];
}

export interface Viability {
  verdict: 'viable' | 'thin' | 'not_viable' | 'no_market';
  product?: string;
  n?: number;
  market?: MarketBand;
  breakEven?: number;
  markupPrice?: number;
  unitCost?: number;
  atMedian?: { price: number; fees: number; margin: number; margin_pct: number | null } | null;
  reason?: string;
}

export interface PriceCandidate {
  label: string;
  price: number;
  net_of_vat: number;
  fees: number;
  margin: number;
  margin_pct: number | null;
  position_in_band: string;
  rejected_because?: string;
  below_floor_by?: number;
}

export interface PriceProposal {
  price: number;
  currency: string;
  candidate: string;
  position: 'budget' | 'mid' | 'premium';
  rationale: string;
  runner_up?: { candidate: string; price: number; why_not: string } | null;
  floor?: number;
  override?: { by: string; reason: string } | null;
}

export interface Pricing {
  floor: number | null;
  minMarginPct: number | null;
  candidates: PriceCandidate[];
  rejectedCandidates: PriceCandidate[];
  proposal: PriceProposal | null;
  /** The script's reason when no candidate was allowed. */
  rejected: string | null;
}

export interface Comparable {
  title: string;
  url: string;
  price: number;
  sold: number | null;
  favorers: number | null;
  reason: string;
  image: string | null;
}

export interface Job {
  id: string;
  fileName: string;
  stage: JobStage;
  status: JobStatus;
  createdAt: string;
  updatedAt: string;
  /** Set when status is 'blocked' or 'failed', or a one-line note while waiting. */
  reason?: string | null;
  stagesDone: string[];
  /** A foreign slicer project had its settings removed before dapi saw it. */
  stripped: boolean;
  sourceApplication?: string | null;
  runs: string[];
  /** dapi quote, present once the slice stage is done. */
  price?: number;
  quoteId?: string;
  batch?: Quote['batch'];
  metrics?: QuoteMetrics | null;
  analysis?: { bbox_mm: number[]; volume_mm3: number; bodies: number; watertight_bodies: number; triangles: number };
  // Only on GET /jobs/{id}:
  viability?: Viability;
  comparables?: { product: string; queries: string[]; n: number; rejected: number; top: Comparable[] };
  pricing?: Pricing;
  renders?: { drafts: string[]; hero: string | null; angles: string[]; video: string | null };
  design?: Design;
  log?: string;
}

export interface JobEvent {
  stage: JobStage;
  attempt: number;
  status: JobStatus;
  durationMs: number;
  at: string;
}

export const ACCEPTED_EXTENSIONS = ['.stl', '.3mf', '.obj'] as const;

export interface CreateJobsResult {
  jobs: Job[];
  failures: { fileName: string; reason: string }[];
}

/**
 * One request per file, so a rejected upload is a fact about that file and
 * the others still go in. Each request starts its own run on the server.
 */
export async function createJobs(
  files: File[],
  onStep?: (index: number, step: string) => void,
): Promise<CreateJobsResult> {
  const jobs: Job[] = [];
  const failures: CreateJobsResult['failures'] = [];
  for (const [index, file] of files.entries()) {
    onStep?.(index, 'uploading');
    const form = new FormData();
    form.append('files', file, file.name);
    try {
      const created = await api<Job[]>('/jobs', { method: 'POST', body: form });
      jobs.push(...created);
      onStep?.(index, 'started');
    } catch (error) {
      failures.push({ fileName: file.name, reason: error instanceof Error ? error.message : String(error) });
    }
  }
  return { jobs, failures };
}

export const listJobs = (): Promise<Job[]> => api<Job[]>('/jobs');

export const getJob = (id: string): Promise<Job> => api<Job>(`/jobs/${encodeURIComponent(id)}`);

export const getJobEvents = (id: string): Promise<JobEvent[]> =>
  api<JobEvent[]>(`/jobs/${encodeURIComponent(id)}/events`);

export const approveJob = (id: string): Promise<Job> =>
  api<Job>(`/jobs/${encodeURIComponent(id)}/approve`, { method: 'POST' });

export interface Run {
  run: string;
  started: string | null;
  department: string | null;
  items: string[];
  files: (string | null)[];
  counts: Record<JobStatus, number>;
  cost: { tokens?: number; usd?: number };
  alive: boolean;
  report: string | null;
  notes: string[];
}

export const listRuns = (): Promise<Run[]> => api<Run[]>('/runs');
export const listUnfinished = (): Promise<{ items: string[]; count: number }> => api('/runs/unfinished');
export const resumeRuns = (): Promise<{ ok: boolean; run: string | null; items: string[] }> =>
  api('/runs/resume', { method: 'POST' });

/** True while the server may still change this job without being asked. */
export const isLive = (job: Job | undefined): boolean =>
  job?.status === 'running' || job?.status === 'queued';
