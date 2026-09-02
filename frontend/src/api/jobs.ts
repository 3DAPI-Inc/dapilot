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

export const listJobs     = ()           => api<Job[]>('/jobs');
export const getJob       = (id: string) => api<Job>(`/jobs/${id}`);
export const getJobEvents = (id: string) => api<JobEvent[]>(`/jobs/${id}/events`);
export const approveJob   = (id: string) => api<Job>(`/jobs/${id}/approve`, { method: 'POST' });
