import type { JobStage, JobStatus } from '@/api/jobs';

/** The nine intake stages, in order. Mirrors api/jobs.ts and stages.json. */
export const STAGES: JobStage[] = [
  'intake', 'analyze', 'slice', 'preview', 'research', 'price', 'render', 'copy', 'publish',
];

/**
 * Said from the operator's side, not the system's. `blocked` is the pipeline's
 * word for "a gate is waiting on a human", so the row says what the human has
 * to do about it.
 */
export const STATUS_LABEL: Record<JobStatus, string> = {
  queued: 'queued',
  running: 'running',
  blocked: 'needs you',
  done: 'done',
  failed: 'failed',
};
