import type { BufferGeometry } from 'three';
import { unzipSync } from 'fflate';
import { flattenToArrays, toGeometry } from './geometry';
import type { ParseFormat, ParseRequest, ParseResponse } from './parse.worker';

/*
  One model file in, something previewable out.

  Two shapes come back. Normally it is geometry the viewer can light and orbit.
  For a .3mf too big to parse in the tab it is Bambu Studio's own plate render,
  which every .3mf carries at Metadata/plate_1.png: a free, instant thumbnail
  that beats locking the browser up for thirty seconds.
*/

export type Preview =
  | { kind: 'geometry'; geometry: BufferGeometry }
  | { kind: 'image'; url: string; note: string };

/** Bambu Studio writes these on export. plate_1 is the bed, top_1 is from above. */
const EMBEDDED_PREVIEWS = ['Metadata/plate_1.png', 'Metadata/top_1.png'];

/**
 * Above this a .3mf is shown through its embedded render instead of parsed.
 * three's 3MF loader builds an XML DOM and a JS object per vertex, so cost
 * climbs far faster than file size and it cannot be moved off the main thread.
 */
const THREEMF_PARSE_LIMIT = 32 * 1024 * 1024;

export function extensionOf(fileName: string): string {
  const dot = fileName.lastIndexOf('.');
  return dot === -1 ? '' : fileName.slice(dot).toLowerCase();
}

/** Rejects with an AbortError if `signal` fires first. */
function parseInWorker(format: ParseFormat, buffer: ArrayBuffer, signal: AbortSignal): Promise<BufferGeometry> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./parse.worker.ts', import.meta.url), { type: 'module' });

    const settle = (finish: () => void) => {
      // terminate() is the cancellation: a worker halfway through a 200 MB STL
      // has no other way to be told the user moved on.
      worker.terminate();
      signal.removeEventListener('abort', onAbort);
      finish();
    };
    const onAbort = () => settle(() => reject(signal.reason));
    signal.addEventListener('abort', onAbort);

    worker.onmessage = ({ data }: MessageEvent<ParseResponse>) =>
      settle(() => (data.ok ? resolve(toGeometry(data)) : reject(new Error(data.error))));
    worker.onerror = (event) =>
      settle(() => reject(new Error(event.message || 'the model parser crashed')));

    // The buffer is transferred, so this file's bytes are read exactly once.
    worker.postMessage({ format, buffer } satisfies ParseRequest, [buffer]);
  });
}

/** The render Bambu already made, pulled out of the zip without inflating the rest. */
async function embeddedPreview(file: File): Promise<string | null> {
  const zip = new Uint8Array(await file.arrayBuffer());
  for (const name of EMBEDDED_PREVIEWS) {
    const found = unzipSync(zip, { filter: (entry) => entry.name === name })[name];
    if (found?.length) {
      return URL.createObjectURL(new Blob([found.slice().buffer], { type: 'image/png' }));
    }
  }
  return null;
}

const OVERSIZE_NOTE = "Too large to open in the browser. This is Bambu Studio's own plate render, read straight out of the file.";
const FALLBACK_NOTE = "The geometry would not parse. This is Bambu Studio's own plate render, read straight out of the file.";

/** Null when the file carries no render. Never leaves an object URL behind on abort. */
async function imagePreview(file: File, note: string, signal: AbortSignal): Promise<Preview | null> {
  const url = await embeddedPreview(file);
  if (signal.aborted) {
    if (url) URL.revokeObjectURL(url);
    signal.throwIfAborted();
  }
  return url ? { kind: 'image', url, note } : null;
}

async function load3mf(file: File, signal: AbortSignal): Promise<Preview> {
  if (file.size > THREEMF_PARSE_LIMIT) {
    const preview = await imagePreview(file, OVERSIZE_NOTE, signal);
    if (preview) return preview;
  }

  const buffer = await file.arrayBuffer();
  signal.throwIfAborted();
  try {
    // Imported here so a session that never stages a .3mf never downloads it.
    const { ThreeMFLoader } = await import('three/examples/jsm/loaders/3MFLoader.js');
    signal.throwIfAborted();
    return { kind: 'geometry', geometry: toGeometry(flattenToArrays(new ThreeMFLoader().parse(buffer))) };
  } catch (error) {
    const preview = await imagePreview(file, FALLBACK_NOTE, signal);
    if (preview) return preview;
    throw error;
  }
}

export async function loadPreview(file: File, signal: AbortSignal): Promise<Preview> {
  const extension = extensionOf(file.name);
  if (extension === '.3mf') return load3mf(file, signal);
  if (extension !== '.stl' && extension !== '.obj' && extension !== '.ply') {
    throw new Error(`no preview for ${extension || 'a file with no extension'}`);
  }

  const buffer = await file.arrayBuffer();
  signal.throwIfAborted();
  return { kind: 'geometry', geometry: await parseInWorker(extension.slice(1) as ParseFormat, buffer, signal) };
}
