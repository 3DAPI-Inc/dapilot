import { api } from './client';

/*
  Social posts for one product. The server owns every step: the words live in
  work/<hash>/social/posts.json, the media is what the Etsy listing already
  carries, and pressing Post is the person's approval that social_post.py
  needs before anything public happens.
*/

export type SocialPostState = 'draft' | 'pending' | 'posted';

export interface SocialPost {
  /** platform.format, e.g. instagram.reel */
  id: string;
  platform: string;
  format: 'reel' | 'carousel' | 'image';
  caption: string;
  hashtags: string[];
  media: { kind: 'image' | 'video'; url: string | null }[];
  /** What the platform would refuse, from the script's check. Empty means it can be posted. */
  errors: string[];
  state: SocialPostState;
  url: string | null;
  postedAt: string | null;
  metrics: Record<string, number> | null;
}

export interface SocialSession {
  platform: string;
  account: string;
  state: 'ok' | 'expiring' | 'needs_login' | 'challenge';
  note: string | null;
}

export interface Social {
  /** False until the Etsy listing has media: posts are made of the listing's own files. */
  ready: boolean;
  reason: string | null;
  listingUrl: string | null;
  posts: SocialPost[];
  sessions: SocialSession[];
}

const base = (jobId: string) => `/jobs/${encodeURIComponent(jobId)}/social`;

export const getSocial = (jobId: string): Promise<Social> => api<Social>(base(jobId));

export const saveSocialPost = (jobId: string, postId: string, body: { caption: string; hashtags: string[] }): Promise<Social> =>
  api<Social>(`${base(jobId)}/posts/${encodeURIComponent(postId)}`, { method: 'PUT', body: JSON.stringify(body) });

export const publishSocialPost = (jobId: string, postId: string): Promise<{ result: { pending?: boolean; url?: string }; social: Social }> =>
  api(`${base(jobId)}/posts/${encodeURIComponent(postId)}/publish`, { method: 'POST', body: JSON.stringify({ by: 'console' }) });

export const refreshSocialMetrics = (jobId: string): Promise<Social> => api<Social>(`${base(jobId)}/metrics`, { method: 'POST' });
