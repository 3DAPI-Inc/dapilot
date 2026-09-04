import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import { OBJLoader } from 'three/examples/jsm/loaders/OBJLoader.js';
import { flattenToArrays, tighten, type MeshArrays } from './geometry';

/*
  Mesh parsing, off the main thread.

  A 4 MB STL is a quarter of a million triangles and parsing it on the main
  thread freezes the tab, including the spinner that is supposed to say the app
  is busy. .3mf is not handled here: three's 3MF loader needs DOMParser, which
  a worker does not have, so load.ts keeps that one on the main thread.
*/

export type ParseFormat = 'stl' | 'obj';
export type ParseRequest = { format: ParseFormat; buffer: ArrayBuffer };
export type ParseResponse = ({ ok: true } & MeshArrays) | { ok: false; error: string };

// Typed by hand rather than by pulling in the webworker lib, which collides
// with lib.dom over half the globals it declares.
const ctx = self as unknown as {
  postMessage(message: ParseResponse, transfer?: Transferable[]): void;
  addEventListener(type: 'message', handler: (event: MessageEvent<ParseRequest>) => void): void;
};

function parse({ format, buffer }: ParseRequest): MeshArrays {
  if (format === 'stl') {
    // STLLoader reads both the binary and the ascii flavour and always returns
    // one non-indexed geometry with per-face normals.
    const geometry = new STLLoader().parse(buffer);
    if (!geometry.getAttribute('normal')) geometry.computeVertexNormals();
    return {
      positions: tighten(geometry.getAttribute('position').array as Float32Array),
      normals: tighten(geometry.getAttribute('normal').array as Float32Array),
    };
  }
  return flattenToArrays(new OBJLoader().parse(new TextDecoder().decode(buffer)));
}

ctx.addEventListener('message', (event) => {
  try {
    const { positions, normals } = parse(event.data);
    // Deduped: an empty mesh could hand back the same buffer twice, and a
    // repeated entry in the transfer list is a DataCloneError.
    const transfer = [...new Set<Transferable>([positions.buffer, normals.buffer])];
    ctx.postMessage({ ok: true, positions, normals }, transfer);
  } catch (error) {
    ctx.postMessage({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});
