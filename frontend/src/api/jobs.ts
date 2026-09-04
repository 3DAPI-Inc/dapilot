import { type Quote, getQuote, listQuotes, quoteFile } from './dapi';

// Mirrors plugins/dapilot/skills/run-intake/reference/stages.json. Keep in sync.
export type JobStage =
  | 'intake' | 'analyze' | 'slice' | 'price'
  | 'render' | 'research' | 'copy' | 'publish';

export type JobStatus = 'queued' | 'running' | 'blocked' | 'done' | 'failed';

export interface Job {
  id: string;
  fileName: string;
  stage: JobStage;
  status: JobStatus;
  createdAt: string;
  updatedAt: string;
  /** Set when status is 'blocked' or 'failed', or a one-line note while running. */
  reason?: string;
  /** dapi quote, present once stage 'price' is done. */
  price?: number;
  quoteId?: string;
  batch?: Quote['batch'];
}

export interface JobEvent {
  stage: JobStage;
  attempt: number;
  status: JobStatus;
  durationMs: number;
  at: string;
}

export const ACCEPTED_EXTENSIONS = ['.stl', '.3mf', '.obj'] as const;

/**
 * A job is a dapi quote seen from the operator's side. Quotes are named with
 * the file's sha256 by quoteFile(), so the name is not a filename; the UI
 * remembers filenames per hash in localStorage for the rows it created.
 */
const NAMES_KEY = 'dapilot.fileNames';
function fileNames(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(NAMES_KEY) ?? '{}'); } catch { return {}; }
}
function rememberName(hash: string, fileName: string) {
  try { localStorage.setItem(NAMES_KEY, JSON.stringify({ ...fileNames(), [hash]: fileName })); } catch { /* fine */ }
}

export function toJob(q: Quote): Job {
  const names = fileNames();
  const base = {
    id: q.publicId,
    fileName: (q.name && names[q.name]) || q.name?.slice(0, 12) || q.publicId,
    createdAt: q.createdAt,
    updatedAt: q.finishedAt ?? q.createdAt,
    quoteId: q.publicId,
  };
  switch (q.status) {
    case 'DONE':
      return { ...base, stage: 'price', status: 'done', price: q.result ?? undefined, batch: q.batch ?? null };
    case 'ERROR':
      return { ...base, stage: 'slice', status: 'failed', reason: q.errorMessage ?? 'slicer error' };
    case 'CANCELLED':
      return { ...base, stage: 'slice', status: 'failed', reason: 'cancelled' };
    case 'FILE_UPLOADED':
      return { ...base, stage: 'slice', status: 'queued', reason: 'waiting for the slicer' };
    default:
      return { ...base, stage: 'slice', status: 'running', reason: 'slicing on dapi' };
  }
}

export interface CreateJobsResult {
  jobs: Job[];
  failures: { fileName: string; reason: string }[];
}

/**
 * Uploads each file to dapi and creates a quote for it (or reuses one with the
 * same hash). One bad file does not take the batch down with it: a rejected
 * upload is a fact about that file, and the others were already paid for.
 */
export async function createJobs(
  files: File[],
  onStep?: (index: number, step: string) => void,
): Promise<CreateJobsResult> {
  const jobs: Job[] = [];
  const failures: CreateJobsResult['failures'] = [];
  for (const [index, file] of files.entries()) {
    try {
      const q = await quoteFile(file, (step) => onStep?.(index, step));
      if (q.name) rememberName(q.name, file.name);
      jobs.push(toJob(q));
    } catch (error) {
      failures.push({
        fileName: file.name,
        reason: error instanceof Error ? error.message : String(error),
      });
    }
  }
  return { jobs, failures };
}

export const listJobs = async (): Promise<Job[]> => {
  const res = await listQuotes(1, 100);
  return res.items.map(toJob).sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1));
};

export const getJob = async (id: string): Promise<Job> => toJob(await getQuote(id));

/** dapi exposes no per-stage log; derive the two events a quote implies. */
export const getJobEvents = async (id: string): Promise<JobEvent[]> => {
  const q = await getQuote(id);
  const events: JobEvent[] = [{ stage: 'intake', attempt: 1, status: 'done', durationMs: 0, at: q.createdAt }];
  if (q.finishedAt) {
    events.push({
      stage: 'slice', attempt: 1, status: q.status === 'DONE' ? 'done' : 'failed',
      durationMs: new Date(q.finishedAt).getTime() - new Date(q.createdAt).getTime(), at: q.finishedAt,
    });
  }
  return events;
};

export const approveJob = async (_id: string): Promise<Job> => {
  throw new Error('nothing to approve yet: no publish route exists on the public API');
};
