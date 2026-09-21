import type { JobStage, JobStatus } from '@/api/jobs';

/** The nine intake stages, in order. Mirrors api/jobs.ts and stages.json. */
export const STAGES: JobStage[] = [
  'intake', 'analyze', 'slice', 'preview', 'research', 'price', 'design', 'copy', 'publish',
];

/**
 * Said from the operator's side, not the system's. `blocked` is the pipeline's
 * word for "a gate is waiting on a human", so the row says what the human has
 * to do about it.
 */
/** Stage and status as one phrase a person would say: "Price needs you", "Queued for design". */
export function statusPhrase(stage: JobStage, status: JobStatus): string {
  const name = stage.charAt(0).toUpperCase() + stage.slice(1);
  if (status === 'queued') return `Queued for ${stage}`;
  if (status === 'blocked') return `${name} needs you`;
  return `${name} ${status}`;
}

export const STATUS_LABEL: Record<JobStatus, string> = {
  queued: 'queued',
  running: 'running',
  blocked: 'needs you',
  done: 'done',
  failed: 'failed',
};
