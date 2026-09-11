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
*/

export interface CaptureOptions {
  /** Frames in one full rotation. 120 at 3 degree steps is the usual. */
  frames: number;
  /** Square output size in pixels, independent of the window. */
  size: number;
  passes: Pass[];
  /** Where each frame is posted. Empty means dry run, nothing leaves the tab. */
  post: string;
  /** Degrees added to every frame, so the sequence starts on the hero. */
  startYaw: number;
  /**
   * Total degrees swept across the sequence. 360 is a turntable; a small value
   * is a slow drift, which is what a still lifestyle scene wants. The sweep is
   * centred on startYaw so the hero angle stays the middle of the move.
   */
  arc: number;
}

export interface CaptureProgress {
  done: number;
  total: number;
  pass: Pass;
  yaw: number;
}

/**
 * Runs to completion and resolves when every frame has been accepted. Rejects
 * on the first frame the collector refuses, because a sequence with a hole in
 * it is worse than no sequence.
 */
export async function captureTurntable(
  studio: Studio,
  options: CaptureOptions,
  onProgress?: (p: CaptureProgress) => void,
): Promise<{ frames: number; passes: Pass[]; size: number }> {
  const { frames, size, passes, post, startYaw, arc } = options;
  const held = studio.yaw();
  studio.setSize(size);

  let done = 0;
  const total = frames * passes.length;
  try {
    for (const pass of passes) {
      studio.setPass(pass);
      for (let i = 0; i < frames; i++) {
        const yaw = (startYaw - arc / 2 + (arc * i) / Math.max(frames - 1, 1) + 360) % 360;
        studio.setYaw(yaw);
        const blob = await studio.captureBlob();
        if (post) {
          const res = await fetch(`${post}?pass=${pass}&index=${i}`, { method: 'POST', body: blob });
          if (!res.ok) throw new Error(`frame ${i} of ${pass}: collector said ${res.status}`);
        }
        done += 1;
        onProgress?.({ done, total, pass, yaw });
      }
    }
  } finally {
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
  return {
    frames: Math.max(1, Number(q.get('frames') ?? 120)),
    size: Math.max(64, Number(q.get('size') ?? 720)),
    passes: passes.length ? passes : ['beauty'],
    post: q.get('post') ?? '',
    startYaw: Number(q.get('startYaw') ?? 0),
    arc: Number(q.get('arc') ?? 360),
  };
}
