/**
 * Direct client for dapi's public API, called from the browser.
 *
 * TEMPORARY: the API key is read from VITE_DAPI_API_KEY and therefore ships in
 * the bundle. This breaks the "no credential reaches the browser" rule in
 * CLAUDE.md by explicit decision (2026-09-04) for internal development against
 * staging. server/app.py is the key-holding alternative when this is revisited.
 */

const BASE = (import.meta.env.VITE_DAPI_API_URL ?? '').replace(/\/+$/, '').replace(/\/v1$/, '');
const KEY = import.meta.env.VITE_DAPI_API_KEY ?? '';

export type QuoteStatus = 'FILE_UPLOADED' | 'PROCESSING' | 'DONE' | 'CANCELLED' | 'ERROR';

export interface Quote {
  publicId: string;
  name: string | null;
  status: QuoteStatus;
  result: number | null;
  batch?: { max_per_plate: number; prices: { quantity: number; unit: number; total: number; plates: number }[] } | null;
  errorMessage?: string;
  createdAt: string;
  finishedAt: string | null;
}

export class DapiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'DapiError';
    this.status = status;
  }
}

/** Where calls actually go, so an unreachable API can name itself in the UI. */
export const apiUrl = BASE || '(unset)';

export function configured(): boolean {
  return Boolean(BASE && KEY);
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  if (!configured()) throw new DapiError(0, 'VITE_DAPI_API_URL or VITE_DAPI_API_KEY is not set in frontend/.env');
  const res = await fetch(`${BASE}/v1${path}`, {
    ...init,
    headers: { 'x-api-key': KEY, accept: 'application/json', ...(init?.body ? { 'content-type': 'application/json' } : {}), ...init?.headers },
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      message = body.message ?? message;
    } catch { /* not JSON */ }
    throw new DapiError(res.status, message);
  }
  return (await res.json()) as T;
}

/** Content hash, the same key the pipeline uses for work/<sha256>/ and quote names. */
export async function sha256(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, '0')).join('');
}

export const listQuotes = (page = 1, pageSize = 100) =>
  call<{ items: Quote[]; page: number; page_size: number; total: number }>(`/quotes?page=${page}&page_size=${pageSize}`);

export const getQuote = (publicId: string) => call<Quote>(`/quotes/${encodeURIComponent(publicId)}`);

/** Find a live quote already made for this content hash, so a file is never sliced twice. */
export async function findQuoteByName(name: string): Promise<Quote | null> {
  for (let page = 1; page <= 20; page++) {
    const res = await listQuotes(page, 100);
    const hit = res.items.find((q) => q.name === name && q.status !== 'ERROR' && q.status !== 'CANCELLED');
    if (hit) return hit;
    if (res.items.length < 100 || page * 100 >= res.total) return null;
  }
  return null;
}

/** upload_file -> PUT to S3 -> POST /quotes. Returns the quote, existing or new. */
export async function quoteFile(file: File, onStep?: (step: string) => void): Promise<Quote> {
  const name = await sha256(file);
  onStep?.('checking');
  const existing = await findQuoteByName(name);
  if (existing) return existing;

  onStep?.('uploading');
  const up = await call<{ publicId: string; upload_url: string; s3_key: string; expires_in: number }>(
    '/quotes/upload_file', { method: 'POST', body: JSON.stringify({ file_name: file.name }) },
  );
  const put = await fetch(up.upload_url, { method: 'PUT', body: file, headers: { 'content-type': 'application/octet-stream' } });
  if (!put.ok) throw new DapiError(put.status, `S3 upload failed: ${put.statusText}`);

  onStep?.('slicing');
  const created = await call<{ publicId: string; status: 'PROCESSING' | 'ERROR' }>(
    '/quotes', { method: 'POST', body: JSON.stringify({ s3_key: up.s3_key, name }) },
  );
  return getQuote(created.publicId);
}
