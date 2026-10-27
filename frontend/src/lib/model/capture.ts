import type { Pass, Studio } from './studio';

/*
  The capture loop.

  A turntable is stepped, never recorded. Each frame sets the yaw from its
  index, renders, and is posted before the next one starts, so the output is
  exactly N frames at exactly the angles asked for no matter how fast the
  machine draws. Recording in real time would tie the result to the frame rate
  of whatever ran it.

  It posts frames to a local collector rather than downloading them, because a
  headless browser has nowhere to download to and the driver needs to know when
  the run finished. The driver is capture_turntable.py.

  It also reports progress (2026-10-26). A software renderer on a small server
  takes minutes for one 2048 px frame, and a driver that hears nothing between
  frames cannot tell slow from dead: the Benchy's stills were given up after
  one frame three times running. So the loop says what it is doing, to the
  `progress` URL when the driver gave one: a word before every frame, a word
  every few seconds while one is drawn or encoded, the reason when it fails,
  and done at the end. The driver reads those instead of guessing.
*/

export interface CaptureOptions {
  /** Frames in one full rotation. 120 at 3 degree steps is the usual. */
  frames: number;
  /** Square output size in pixels, independent of the window. */
  size: number;
  passes: Pass[];
  /** Where each frame is posted. Empty means dry run, nothing leaves the tab. */
  post: string;
  /** Where progress is posted, as JSON. Empty means nobody is listening. */
  progress?: string;
  /** Degrees added to every frame, so the sequence starts on the hero. */
  startYaw: number;
  /**
   * Total degrees swept across the sequence. 360 is a turntable; a small value
   * is a slow drift, which is what a still lifestyle scene wants. The sweep is
   * centred on startYaw so the hero angle stays the middle of the move.
   */
  arc: number;
  /**
   * Exact angles, one frame each, in order. When set it replaces frames,
   * startYaw and arc: product stills want chosen angles, not an even sweep.
   */
  yaws?: number[];
}

export interface CaptureProgress {
  done: number;
  total: number;
  pass: Pass;
  yaw: number;
}

/** What one progress report says. `state` is the only field the driver needs; the rest is for a log line. */
export interface ProgressReport {
  state: 'alive' | 'frame' | 'done' | 'failed';
  /** What the loop is doing right now, for a frame or alive report. */
  phase?: 'render' | 'encode' | 'post';
  /** The frame being worked on, from 0, and its pass and yaw. */
  index?: number;
  pass?: Pass;
  yaw?: number;
  /** Frames accepted so far, of how many in all. */
  done?: number;
  total?: number;
  /** Milliseconds spent on the current frame so far. */
  frame_ms?: number;
  /** failed: why, in one sentence. */
  reason?: string;
}

/** How often the loop says it is alive while one frame is drawn, in ms. */
export const HEARTBEAT_MS = 5000;

/**
 * The heartbeat runs in a Web Worker, not on a timer in this thread. Reading
 * a frame back from WebGL blocks the main thread until the renderer has
 * finished drawing it, which on a software renderer is the whole frame time,
 * so a setInterval here never fired while a frame was made (measured
 * 2026-10-26: every report said 0 s). A worker has its own thread and its
 * own fetch; it is told the current frame before each one and beats on its
 * own until the capture ends. If the tab dies, the worker dies with it and
 * the silence is real.
 */
const HEARTBEAT_SOURCE = `
  let url = '';
  let info = {};
  let since = Date.now();
  onmessage = (e) => {
    if (e.data.url) url = e.data.url;
    if (e.data.info) { info = e.data.info; since = Date.now(); }
    if (e.data.stop) { close(); }
  };
  setInterval(() => {
    if (!url) return;
    fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ...info, state: 'alive', frame_ms: Date.now() - since }),
    }).catch(() => {});
  }, ${HEARTBEAT_MS});
`;

function startHeartbeat(url: string | undefined): { tell: (info: ProgressReport) => void; stop: () => void } {
  if (!url || url === 'parent' || typeof Worker === 'undefined') return { tell: () => {}, stop: () => {} };
  try {
    const blob = new Blob([HEARTBEAT_SOURCE], { type: 'text/javascript' });
    const worker = new Worker(URL.createObjectURL(blob));
    worker.postMessage({ url });
    return {
      tell: (info) => worker.postMessage({ info }),
      stop: () => { worker.postMessage({ stop: true }); worker.terminate(); },
    };
  } catch {
    return { tell: () => {}, stop: () => {} };
  }
}

/**
 * Posts one report and never throws: progress is a courtesy to the driver,
 * and a collector that is gone must not stop a frame that is being made.
 * `keepalive` lets the last report leave a page that is closing.
 */
export async function report(url: string | undefined, body: ProgressReport): Promise<void> {
  if (!url) return;
  // `parent`: this page is a hidden iframe of the dashboard (2026-10-26), which reads the reports and
  // the frames from window messages and uploads the frames itself; nothing leaves this origin.
  if (url === 'parent') {
    try {
      window.parent.postMessage({ type: 'dapilot-progress', ...body }, '*');
    } catch {
      /* no parent */
    }
    return;
  }
  try {
    await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
      keepalive: true,
    });
  } catch {
    /* nobody listening */
  }
}

/**
 * Runs to completion and resolves when every frame has been accepted. Rejects
 * on the first frame the collector refuses, because a sequence with a hole in
 * it is worse than no sequence. The failure is reported before it is thrown.
 */
export async function captureTurntable(
  studio: Studio,
  options: CaptureOptions,
  onProgress?: (p: CaptureProgress) => void,
): Promise<{ frames: number; passes: Pass[]; size: number }> {
  const { size, passes, post, progress, startYaw, arc, yaws } = options;
  const frames = yaws?.length || options.frames;
  const held = studio.yaw();
  studio.setSize(size);

  let done = 0;
  const total = frames * passes.length;
  const beat = startHeartbeat(progress);
  beat.tell({ state: 'alive', done, total });
  try {
    for (const pass of passes) {
      studio.setPass(pass);
      for (let i = 0; i < frames; i++) {
        const yaw = yaws?.length
          ? (((yaws[i] % 360) + 360) % 360)
          : (startYaw - arc / 2 + (arc * i) / Math.max(frames - 1, 1) + 360) % 360;
        const current: ProgressReport = { state: 'frame', phase: 'render', index: i, pass, yaw, done, total };
        beat.tell(current);
        void report(progress, current);
        studio.setYaw(yaw);
        const blob = await studio.captureBlob();
        if (post === 'parent') {
          window.parent.postMessage({ type: 'dapilot-frame', pass, index: i, yaw, blob }, '*');
        } else if (post) {
          beat.tell({ ...current, phase: 'post' });
          const res = await fetch(`${post}?pass=${pass}&index=${i}`, { method: 'POST', body: blob });
          if (!res.ok) throw new Error(`frame ${i} of ${pass}: collector said ${res.status}`);
        }
        done += 1;
        onProgress?.({ done, total, pass, yaw });
      }
    }
    await report(progress, { state: 'done', done, total });
  } catch (e) {
    await report(progress, { state: 'failed', reason: e instanceof Error ? e.message : String(e), done, total });
    throw e;
  } finally {
    beat.stop();
    studio.setSize(null);
    studio.setPass('beauty');
    studio.setYaw(held);
  }
  return { frames, passes, size };
}

/** Read from the query string, so the driver can ask for a run with a URL. */
export function captureFromQuery(search: string): CaptureOptions | null {
  const q = new URLSearchParams(search);
  if (q.get('capture') !== '1') return null;
  const passes = (q.get('passes') ?? 'beauty,depth,normal')
    .split(',')
    .map((p) => p.trim())
    .filter((p): p is Pass => p === 'beauty' || p === 'depth' || p === 'normal');
  const yaws = (q.get('yaws') ?? '')
    .split(',')
    .filter((v) => v.trim() !== '')
    .map(Number)
    .filter((v) => Number.isFinite(v));
  return {
    frames: Math.max(1, Number(q.get('frames') ?? 120)),
    size: Math.max(64, Number(q.get('size') ?? 720)),
    passes: passes.length ? passes : ['beauty'],
    post: q.get('post') ?? '',
    progress: q.get('progress') ?? undefined,
    startYaw: Number(q.get('startYaw') ?? 0),
    arc: Number(q.get('arc') ?? 360),
    ...(yaws.length ? { yaws, frames: yaws.length } : {}),
  };
}
