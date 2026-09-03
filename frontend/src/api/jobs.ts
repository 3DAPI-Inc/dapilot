import { api } from './client';

// Mirrors the stage registry in protocol/pipeline.md. Keep in sync.
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
  /** Set when status is 'blocked' or 'failed'. */
  reason?: string;
}

export interface JobEvent {
  stage: JobStage;
  attempt: number;
  status: JobStatus;
  durationMs: number;
  at: string;
}

/**
 * Extensions the pipeline actually accepts, from the analyze-model contract in
 * docs/skills.md ("source.stl | source.3mf | source.obj"). Anything else comes
 * back from the skill as `unsupported: <ext>`, so the UI rejects it up front
 * using the same word.
 */
export const ACCEPTED_EXTENSIONS = ['.stl', '.3mf', '.obj'] as const;

/**
 * Starts a job per file.
 *
 * NOTE: this is an assumed contract. The job API on :3100 does not exist yet,
 * and unlike the stage names, `POST /jobs` is not written down anywhere. It
 * follows the convention of the reads below (multipart, field name `files`,
 * the created jobs back). Change it here when the real API lands.
 */
export const createJobs = (files: File[]) => {
  const body = new FormData();
  for (const file of files) body.append('files', file);
  return api<Job[]>('/jobs', { method: 'POST', body });
};

export const listJobs     = ()           => api<Job[]>('/jobs');
export const getJob       = (id: string) => api<Job>(`/jobs/${id}`);
export const getJobEvents = (id: string) => api<JobEvent[]>(`/jobs/${id}/events`);
export const approveJob   = (id: string) => api<Job>(`/jobs/${id}/approve`, { method: 'POST' });
