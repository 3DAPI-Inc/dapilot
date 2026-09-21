import { BufferAttribute, BufferGeometry, type Mesh, type Object3D } from 'three';

/**
 * Geometry plumbing shared by the parse worker and the main thread. Nothing in
 * here touches the DOM, so the worker can import it.
 */

export interface MeshArrays {
  positions: Float32Array;
  normals: Float32Array;
  /** Linear RGB per vertex. Only a multicolour display.ply has them; they replace the single filament colour. */
  colors?: Float32Array;
}

/**
 * A Float32Array that owns its whole buffer, so postMessage can transfer it.
 * A view with an offset would arrive on the other side pointing at the wrong
 * bytes, and three's loaders do not promise tight arrays.
 */
export function tighten(array: ArrayLike<number> & ArrayBufferView): Float32Array {
  if (
    array instanceof Float32Array &&
    array.byteOffset === 0 &&
    array.buffer.byteLength === array.byteLength
  ) {
    return array;
  }
  return Float32Array.from(array as ArrayLike<number>);
}

/**
 * Every mesh under `root`, baked into one position/normal pair in world space.
 *
 * The world-matrix step is not optional for .3mf: Bambu puts the geometry on
 * one object and points the build item at a second, component-only object that
 * carries the transform. Reading the meshes without composing their matrices
 * puts the parts back at the origin on top of each other.
 */
export function flattenToArrays(root: Object3D): MeshArrays {
  root.updateMatrixWorld(true);

  const parts: BufferGeometry[] = [];
  root.traverse((node) => {
    const mesh = node as Mesh;
    if (!mesh.isMesh || !mesh.geometry) return;

    const geometry = mesh.geometry.index ? mesh.geometry.toNonIndexed() : mesh.geometry.clone();
    // Position and normal only. Sources disagree about uv and colour, and
    // concatenating across mismatched attribute sets misaligns the vertices.
    for (const name of Object.keys(geometry.attributes)) {
      if (name !== 'position' && name !== 'normal') geometry.deleteAttribute(name);
    }
    if (!geometry.getAttribute('normal')) geometry.computeVertexNormals();
    // Transforms the normals through the inverse transpose, not the matrix.
    geometry.applyMatrix4(mesh.matrixWorld);
    parts.push(geometry);
  });

  if (parts.length === 0) throw new Error('no mesh in this file');

  const vertices = parts.reduce((n, g) => n + g.getAttribute('position').count, 0);
  const positions = new Float32Array(vertices * 3);
  const normals = new Float32Array(vertices * 3);

  let at = 0;
  for (const geometry of parts) {
    const position = geometry.getAttribute('position');
    positions.set(tighten(position.array as Float32Array), at * 3);
    normals.set(tighten(geometry.getAttribute('normal').array as Float32Array), at * 3);
    at += position.count;
    geometry.dispose();
  }

  return { positions, normals };
}

/** The pair back into something three can draw. */
export function toGeometry({ positions, normals, colors }: MeshArrays): BufferGeometry {
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(positions, 3));
  geometry.setAttribute('normal', new BufferAttribute(normals, 3));
  if (colors) geometry.setAttribute('color', new BufferAttribute(colors, 3));
  return geometry;
}
