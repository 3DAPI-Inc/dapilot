import {
  Box3,
  type BufferGeometry,
  DirectionalLight,
  DoubleSide,
  HemisphereLight,
  MathUtils,
  Mesh,
  MeshStandardMaterial,
  PerspectiveCamera,
  Scene,
  Vector3,
  WebGLRenderer,
} from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

/*
  The turntable, in the browser.

  Imperative on purpose: a WebGL context is expensive and browsers cap how many
  a page may hold, so one viewer is built per mounted panel and the geometry
  inside it is swapped. React owns when it exists, not what it does per frame.

  It is lit like the Blender turntable, not like a game: a wide sky light for
  the form and one key for the edges, no shadows, no ground, nothing behind the
  model. The page is a claim about restraint and the preview has to hold to it.
*/

const FOV = 40;
const SURFACE = 0xd9e4f1;
/** ice, from the design system. The only colour on the page that is not ink. */
const RIM = 0x8eb0e2;

export interface ModelStats {
  /** The bounding box in the file's own axes, before the Z-up correction. */
  size: Vector3;
  triangles: number;
}

export interface Viewer {
  show(geometry: BufferGeometry): ModelStats;
  reset(): void;
  dispose(): void;
}

export function createViewer(mount: HTMLElement): Viewer {
  const renderer = new WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  mount.appendChild(renderer.domElement);
  renderer.domElement.style.display = 'block';
  renderer.domElement.style.width = '100%';
  renderer.domElement.style.height = '100%';

  const scene = new Scene();
  const camera = new PerspectiveCamera(FOV, 1, 0.1, 1000);

  scene.add(new HemisphereLight(0xffffff, 0xb9c9dd, 2.1));
  const key = new DirectionalLight(0xffffff, 2.4);
  key.position.set(1, 1.4, 1.1);
  const rim = new DirectionalLight(RIM, 1.1);
  rim.position.set(-1.2, 0.4, -1);
  scene.add(key, rim);

  const material = new MeshStandardMaterial({
    color: SURFACE,
    roughness: 0.62,
    metalness: 0,
    // Real uploads are open in ways synthetic fixtures never are, and a hole in
    // a culled mesh reads as a hole in the model rather than as one in the file.
    side: DoubleSide,
  });

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.rotateSpeed = 0.9;
  // A first look that moves says "you can move this" without a caption. It
  // stops the moment the user takes over, and never starts if they asked for
  // less motion.
  const mayAutoRotate = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  controls.autoRotateSpeed = 1.1;
  const stopAutoRotate = () => {
    controls.autoRotate = false;
  };
  renderer.domElement.addEventListener('pointerdown', stopAutoRotate);
  renderer.domElement.addEventListener('wheel', stopAutoRotate, { passive: true });

  let mesh: Mesh | null = null;
  let framing = { direction: new Vector3(1, 0.55, 1).normalize(), radius: 1 };

  function resize() {
    const { clientWidth: width, clientHeight: height } = mount;
    if (width === 0 || height === 0) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }

  /** The distance at which a sphere of `radius` fits the shorter of the two fields. */
  function fitDistance(radius: number): number {
    const vertical = MathUtils.degToRad(camera.fov);
    const horizontal = 2 * Math.atan(Math.tan(vertical / 2) * camera.aspect);
    return radius / Math.sin(Math.min(vertical, horizontal) / 2);
  }

  function reset() {
    const distance = fitDistance(framing.radius) * 1.2;
    camera.position.copy(framing.direction).multiplyScalar(distance);
    camera.near = Math.max(distance / 1000, 0.01);
    camera.far = distance * 20;
    camera.updateProjectionMatrix();
    controls.target.set(0, 0, 0);
    controls.minDistance = framing.radius * 0.35;
    controls.maxDistance = distance * 8;
    controls.update();
  }

  function show(geometry: BufferGeometry): ModelStats {
    if (mesh) {
      scene.remove(mesh);
      mesh.geometry.dispose();
    }

    const box = new Box3().setFromBufferAttribute(geometry.getAttribute('position') as never);
    const size = box.getSize(new Vector3());
    const center = box.getCenter(new Vector3());
    geometry.translate(-center.x, -center.y, -center.z);
    geometry.computeBoundingSphere();

    mesh = new Mesh(geometry, material);
    // Slicers and CAD are Z-up, three is Y-up. Rotating the object rather than
    // the geometry keeps the reported size in the file's own axes.
    mesh.rotation.x = -Math.PI / 2;
    scene.add(mesh);

    framing = { ...framing, radius: geometry.boundingSphere?.radius || 1 };
    controls.autoRotate = mayAutoRotate;
    resize();
    reset();

    return { size, triangles: geometry.getAttribute('position').count / 3 };
  }

  const observer = new ResizeObserver(resize);
  observer.observe(mount);
  resize();

  let frame = requestAnimationFrame(function tick() {
    frame = requestAnimationFrame(tick);
    controls.update();
    renderer.render(scene, camera);
  });

  return {
    show,
    reset,
    dispose() {
      cancelAnimationFrame(frame);
      observer.disconnect();
      renderer.domElement.removeEventListener('pointerdown', stopAutoRotate);
      renderer.domElement.removeEventListener('wheel', stopAutoRotate);
      controls.dispose();
      mesh?.geometry.dispose();
      material.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
