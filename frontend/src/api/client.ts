/**
 * Thin fetch wrapper for dapilot's own job API.
 *
 * Note what is NOT here: dapi's public API. The browser never calls it
 * directly, because that would put an API key in client-side code. Anything
 * dapilot needs from dapi goes through the pipeline, server side.
 */
const BASE = import.meta.env.VITE_API_URL ?? '/api';

export class ApiError extends Error {
  // Written out rather than as a parameter property: the Vite template sets
  // erasableSyntaxOnly, which rejects the shorthand.
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  // FormData has to set its own Content-Type, because only the browser knows
  // the multipart boundary. Forcing application/json here silently produced a
  // body the server could not parse.
  const isForm = init?.body instanceof FormData;
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: isForm
      ? { ...init?.headers }
      : { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!res.ok) throw new ApiError(res.status, (await res.text()) || res.statusText);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}
