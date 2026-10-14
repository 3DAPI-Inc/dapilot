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
  basis?: 'weighted' | 'unweighted';
  /** The script's call that the band is too small or too wide to trust. */
  thin?: boolean;
  weight_concentration?: { top_shop: string | null; top_shop_share: number; top_listing_share: number } | null;
  /** Interquartile range of the unweighted band after outliers, as a difference and a ratio. */
  iqr?: number;
  iqr_ratio?: number | null;
  /** Prices fenced out of the band (Tukey on log price), kept for the reader with what they were. */
  outliers?: MarketOutlier[];
  outlier_fences?: { low: number; high: number } | null;
  n_collapsed?: number;
  demand?: { units_per_day: number | null; listings_with_estimate: number; listings_with_reviews: number; n: number };
}

export interface MarketOutlier {
  listing_id: number | null;
  price: number;
  side: 'high' | 'low';
  title: string | null;
  shop: string | null;
  materials: string[];
  url: string | null;
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

/** What research decided the object is, and how sure it was. The filename is a witness, not the verdict. */
export interface Identity {
  object: string;
  confidence: 'high' | 'medium' | 'low';
  witnesses: Array<'filename' | 'drafts' | 'thumbnails' | 'analysis'>;
  reason: string | null;
}

export interface Viability {
  verdict: 'viable' | 'thin' | 'not_viable' | 'no_market' | 'unreliable';
  /** Why the band is not evidence: one shop's price, no observed demand, too few listings. */
  verdictReason?: string | null;
  identity?: Identity | null;
  product?: string;
  n?: number;
  /** Per unit of sale since 2026-10-12; `offer.market` is the same band at the pack. */
  market?: MarketBand;
  breakEven?: number;
  markupPrice?: number;
  unitCost?: number;
  atMedian?: { price: number; fees: number; margin: number; margin_pct: number | null } | null;
  /** The pack we list, as research decided it from the comparables. */
  offer?: Offer | null;
  /** The policy scenario at the pack: cost, break-even and margins for one purchase. */
  offerScenario?: { units: number; cost: number; break_even_price: number; markup_price: number } | null;
  reason?: string;
  costBasis?: 'one_off' | 'full_plate';
  /** What a production run costs and how many sales pay it back, per band price. */
  stock?: {
    units: number;
    plates: number;
    unit_cost: number;
    batch_cost: number;
    payback: { label: string; price: number; margin_per_unit: number; units_to_pay_back: number | null; profit_if_all_sold: number }[];
  } | null;
}

export interface PriceCandidate {
  label: string;
  /** The price of one purchase: `units` printed units (a set of six), or one. */
  price: number;
  net_of_vat: number;
  fees: number;
  margin: number;
  margin_pct: number | null;
  position_in_band: string | null;
  how?: string;
  rejected_because?: string;
  below_floor_by?: number;
  units?: number;
  per_unit?: number;
}

/** The unit of sale research decided from the comparables (viability.json / price.candidates.json `offer`). */
export interface Offer {
  units: number;
  source: 'market_modal' | 'single' | string;
  modal_units?: number | null;
  modal_share?: number | null;
  extras_common?: string[];
  note?: string | null;
  /** Cost of one purchase: units times the unit cost. */
  cost?: number;
  /** The market band at the unit of sale. */
  market?: { p25?: number; median?: number; p75?: number } | null;
}

export interface PriceProposal {
  price: number;
  currency: string;
  candidate: string;
  position: 'budget' | 'mid' | 'premium' | null;
  rationale: string;
  runner_up?: { candidate: string; price: number; why_not: string } | null;
  floor?: number;
  override?: { by: string; reason: string; at?: string } | null;
  /** The model's own proposal, kept when a person overrode it. */
  proposed?: Omit<PriceProposal, 'override' | 'proposed'> | null;
}

/** price.approved.json: a person confirmed this price; design and everything after require it. */
export interface PriceApproval {
  price: number;
  currency: string;
  by: string;
  at: string;
  source: 'proposal' | 'override';
  floor: number;
  unit_cost: number;
  margin: number;
  margin_pct: number | null;
  /** Since 2026-10-12: what the price buys. Absent on earlier approvals, which sold singles. */
  offer?: { units: number; per_unit: number } | null;
}

export interface PricePreview {
  price: number;
  net_of_vat: number;
  fees: number;
  unit_cost: number;
  margin: number;
  margin_pct: number | null;
  floor: number;
  aboveFloor: boolean;
  candidate: string | null;
}

export interface Pricing {
  floor: number | null;
  minMarginPct: number | null;
  /** How many printed units one sale brings; null on files from before the unit of sale existed. */
  offer?: Offer | null;
  candidates: PriceCandidate[];
  rejectedCandidates: PriceCandidate[];
  /** Candidates the script did not offer, with the reason (p75 rounding onto another price, no band). */
  skipped: { label: string; reason: string }[];
  /** Who sells at or above the p75. Older files carry the summary fields, newer ones the shop list. */
  p75Sellers: {
    distinct_named_shops?: number;
    top_share?: number;
    unnamed_share?: number;
    shops?: { name: string; weight_share: number }[];
  } | null;
  currency: string;
  proposal: PriceProposal | null;
  /** The script's reason when no candidate was allowed. */
  rejected: string | null;
  approved: PriceApproval | null;
}

export interface Comparable {
  title: string;
  url: string;
  price: number;
  sold: number | null;
  favorers: number | null;
  shop: string | null;
  /** Estimated units sold per day, the weight behind the band. */
  unitsPerDay: number | null;
  reason: string | null;
  image: string | null;
}

export interface Job {
  id: string;
  fileName: string;
  stage: JobStage;
  status: JobStatus;
  createdAt: string;
  updatedAt: string;
  /** True when the item is queued and no director holds it: Continue on the job page starts one. */
  idle?: boolean;
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
  comparables?: { product: string; identity?: Identity | null; queries: string[]; n: number; rejected: number; top: Comparable[] };
  pricing?: Pricing;
  renders?: { drafts: string[]; hero: string | null; angles: string[]; video: string | null };
  /** The 3MF's own previews (plate and top view), served the moment the file is registered. */
  thumbnails?: string[];
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

export const previewPrice = (id: string, price: number): Promise<PricePreview> =>
  api<PricePreview>(`/jobs/${encodeURIComponent(id)}/price/preview?price=${price}`);

/** Approve the recommended price or set another. The server's check decides whether it stands. */
export const decidePrice = (
  id: string,
  body: { price?: number; by?: string; reason?: string },
): Promise<{ job: Job; product: Product | null; run: string | null }> =>
  api(`/jobs/${encodeURIComponent(id)}/price`, { method: 'POST', body: JSON.stringify(body) });

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
/** Takes one item up again: clears a failed or blocked marker if there is one, then a director runs what is left. */
export const retryJob = (id: string): Promise<{ job: Job; cleared: string[]; run: string }> =>
  api(`/jobs/${encodeURIComponent(id)}/retry`, { method: 'POST' });

export const resumeRuns = (): Promise<{ ok: boolean; run: string | null; items: string[] }> =>
  api('/runs/resume', { method: 'POST' });

/**
 * The first preview draft, by the path the server files it under. The list
 * route carries no renders, so a row asks for the picture directly once the
 * preview stage is behind it.
 */
export const draftThumbnail = (job: Job): string | null =>
  job.stagesDone.includes('preview') ? `/api/jobs/${encodeURIComponent(job.id)}/files/renders/draft_00.png` : null;

/** True while the server may still change this job without being asked. */
export const isLive = (job: Job | undefined): boolean =>
  job?.status === 'running' || job?.status === 'queued';

/** A product: an item whose price a person approved. The record lives in the store, not the work dir. */
export interface Product {
  id: string;
  status: 'awaiting_media' | 'ready';
  created_at: string;
  updated_at: string;
  owner: { verified: boolean; api_key_name?: string; note?: string };
  name: string;
  source_file: string;
  price: PriceApproval;
  quote: { publicId: string; unit_price: number | null; max_per_plate: number | null; model_g: number | null };
  order_item: { quoteId: string; filament_slots: number; quantity: number };
  storefront: { platform: string; state: string; listing_id: string | null; url: string | null };
  photos: { id: string; kind: string; url: string }[];
  video: { id: string; url: string } | null;
  thumbnail: string | null;
  pipeline: { stage: JobStage | null; status: JobStatus | null; reason: string | null };
  pendingUploads: number;
}

export const listProducts = (): Promise<Product[]> => api<Product[]>('/products');
