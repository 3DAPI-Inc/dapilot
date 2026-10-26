import {
  AgXToneMapping,
  Box3,
  BufferAttribute,
  BufferGeometry,
  Color,
  DirectionalLight,
  DoubleSide,
  Group,
  MathUtils,
  Mesh,
  MeshNormalMaterial,
  MeshPhysicalMaterial,
  MeshStandardMaterial,
  PCFSoftShadowMap,
  PMREMGenerator,
  PerspectiveCamera,
  RectAreaLight,
  Scene,
  ShaderMaterial,
  Vector3,
  WebGLRenderer,
} from 'three';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { RectAreaLightUniformsLib } from 'three/examples/jsm/lights/RectAreaLightUniformsLib.js';

/*
  The studio, in the browser.

  This is the product-photography scene, not the upload preview. `viewer.ts` is
  the preview and stays deliberately bare; this one has a lightbox, a sweep, a
  contact shadow and a tone curve, because its frames are meant to leave the
  building.

  It is built to be driven rather than watched. The product turns on a pivot
  while camera, lights and sweep stay put, which is the same decision the
  Blender scene makes and for the same reason: every angle is lit identically
  and the sweep is always behind the object.

  Three passes come out of one scene. `beauty` is the photograph. `depth` and
  `normal` are the conditioning maps a diffusion model needs, and they are the
  reason this exists at all: they cost one extra render each because the
  geometry is already on the GPU.
*/

export type Pass = 'beauty' | 'depth' | 'normal';

export interface StudioConfig {
  /** Filament colour, hex. The one thing that changes per product. */
  color: string;
  roughness: number;
  metalness: number;
  /** Clear coat, the faint sheen PLA has. 0 is dead matte. */
  coat: number;

  /** Sweep colour. Null derives it from `color`, the way render_scene.py does. */
  backdrop: string | null;
  /** Ratio the derived sweep must clear before it steps darker. */
  minContrast: number;

  /** Lightbox panels. Intensity is in Three's physical units. */
  keyIntensity: number;
  fillIntensity: number;
  rimIntensity: number;
  /** Ambient wraparound from the generated room. 0 kills the soft fill. */
  environmentIntensity: number;

  /** Contact shadow. Softness is the light's radius in scene units. */
  shadow: boolean;
  shadowOpacity: number;
  shadowSoftness: number;
  /**
   * Side of the shadow map in pixels. 2048 is the console's look; a capture
   * on a machine with no GPU asks for 1024, since the depth pass costs as
   * much as the frame itself when the two are the same size (2026-10-26).
   */
  shadowMapSize: number;
  /**
   * Multisampling on the drawing buffer. Read once, when the renderer is
   * made; setConfig cannot change it. A capture turns it off: on a software
   * renderer the four samples per pixel were most of the cost of a still.
   */
  antialias: boolean;

  /**
   * Orientation in degrees about the file's own x, y, z, applied in that order
   * before framing. The same `--rotate` render_model.py takes, so a choice made
   * from the Blender drafts stands the product up here too.
   */
  rotate: [number, number, number];

  fov: number;
  /** Camera height in degrees above the horizon. */
  elevation: number;
  /** Fraction of the frame left empty on the tightest side. */
  margin: number;
  exposure: number;
}

/**
 * The look. Every value here is meant to be changed; nothing downstream reads
 * a hard-coded number. Defaults track the Blender scene so a browser frame and
 * a Cycles hero read as the same studio.
 */
export const STUDIO_DEFAULTS: StudioConfig = {
  color: '#E8E4DC',
  roughness: 0.42,
  metalness: 0,
  coat: 0.08,

  backdrop: null,
  minContrast: 1.45,

  keyIntensity: 6.5,
  fillIntensity: 1.4,
  rimIntensity: 3.5,
  environmentIntensity: 0.38,

  shadow: true,
  shadowMapSize: 2048,
  antialias: true,
  shadowOpacity: 0.28,
  shadowSoftness: 2.5,

  rotate: [0, 0, 0],
  fov: 28,
  elevation: 18,
  margin: 0.08,
  exposure: 1.0,
};

/* --- backdrop choice: the same arithmetic as plugins/dapilot/scripts/render_scene.py --- */

const BACKDROPS = ['#F2F0EB', '#BFBBB4', '#57544E'];

function luminance(hex: string): number {
  const c = new Color(hex);           // Color parses sRGB and stores linear
  return 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
}

function contrast(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

export function pickBackdrop(productHex: string, minContrast: number): string {
  return BACKDROPS.find((c) => contrast(productHex, c) >= minContrast) ?? BACKDROPS[BACKDROPS.length - 1];
}

/* --- the sweep: floor curving up into a wall, so there is no horizon line --- */

function cyclorama(width = 40, depth = 30, height = 40, radius = 2.5, steps = 16): BufferGeometry {
  // Profile in the (z, y) plane. Camera sits at +z, so the wall is at -z.
  const profile: [number, number][] = [[depth, 0]];
  for (let i = 0; i <= steps; i++) {
    const t = (Math.PI / 2) * (i / steps);
    profile.push([-(2 + radius * Math.sin(t)), radius - radius * Math.cos(t)]);
  }
  profile.push([-(2 + radius), height]);

  const positions: number[] = [];
  const indices: number[] = [];
  for (const [z, y] of profile) {
    positions.push(-width, y, z, width, y, z);
  }
  for (let i = 0; i < profile.length - 1; i++) {
    const a = 2 * i;
    indices.push(a, a + 1, a + 3, a, a + 3, a + 2);
  }
  const g = new BufferGeometry();
  g.setAttribute('position', new BufferAttribute(new Float32Array(positions), 3));
  g.setIndex(indices);
  g.computeVertexNormals();
  return g;
}

/* --- linear depth, white nearest, normalised across the product's own extent ---
   MeshDepthMaterial writes non-linear window depth, which bunches all the
   contrast at the near plane. ControlNet wants the range spread evenly, so
   this shades view-space distance between two uniforms set per frame. */
const DEPTH_SHADER = {
  vertex: `
    varying float vViewDepth;
    void main() {
      vec4 mv = modelViewMatrix * vec4(position, 1.0);
      vViewDepth = -mv.z;
      gl_Position = projectionMatrix * mv;
    }`,
  fragment: `
    uniform float uNear;
    uniform float uFar;
    varying float vViewDepth;
    void main() {
      float d = clamp((vViewDepth - uNear) / max(uFar - uNear, 1e-6), 0.0, 1.0);
      gl_FragColor = vec4(vec3(1.0 - d), 1.0);
    }`,
};

export interface StudioStats {
  /** Bounding box in the file's own axes, millimetres. */
  size: Vector3;
  triangles: number;
  backdrop: string;
}

export interface Studio {
  show(geometry: BufferGeometry): StudioStats;
  setConfig(patch: Partial<StudioConfig>): void;
  config(): StudioConfig;
  setPass(pass: Pass): void;
  /**
   * Turntable position in degrees. The product turns; nothing else moves.
   * Positive yaw turns it clockwise seen from above, as render_scene.py does,
   * so yaw N here frames the same side as the Blender draft at yaw N.
   */
  setYaw(deg: number): void;
  yaw(): number;
  render(): void;
  /** One frame as a PNG data URL, rendered synchronously first. */
  capture(): string;
  /** Same frame as a PNG blob. What the capture loop posts. */
  captureBlob(): Promise<Blob>;
  /**
   * Render at a fixed pixel size instead of following the element. Capture
   * needs the output size pinned so a frame does not depend on the window.
   * Pass null to go back to tracking the layout.
   */
  setSize(px: number | null): void;
  resize(): void;
  dispose(): void;
}

export function createStudio(mount: HTMLElement, overrides: Partial<StudioConfig> = {}): Studio {
  let cfg: StudioConfig = { ...STUDIO_DEFAULTS, ...overrides };
  RectAreaLightUniformsLib.init();

  const renderer = new WebGLRenderer({ antialias: cfg.antialias, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.shadowMap.enabled = cfg.shadow;
  renderer.shadowMap.type = PCFSoftShadowMap;
  // AgX is what the Blender scene uses. Matching it is most of why the two
  // renderers produce pictures that look related.
  renderer.toneMapping = AgXToneMapping;
  renderer.toneMappingExposure = cfg.exposure;
  mount.appendChild(renderer.domElement);
  renderer.domElement.style.display = 'block';
  renderer.domElement.style.width = '100%';
  renderer.domElement.style.height = '100%';

  const scene = new Scene();
  const camera = new PerspectiveCamera(cfg.fov, 1, 0.01, 500);

  // A generated room, used only as an environment map: soft wraparound light
  // with no HDRI file to ship. This is the "lightbox" part of the lightbox.
  const pmrem = new PMREMGenerator(renderer);
  const envRT = pmrem.fromScene(new RoomEnvironment(), 0.04);
  scene.environment = envRT.texture;
  scene.environmentIntensity = cfg.environmentIntensity;

  const key = new RectAreaLight(0xfff6ec, cfg.keyIntensity, 6, 6);
  key.position.set(-3.5, 5.0, 4.0);
  const fill = new RectAreaLight(0xeef4ff, cfg.fillIntensity, 8, 8);
  fill.position.set(4.5, 2.5, 3.5);
  const rim = new RectAreaLight(0xffffff, cfg.rimIntensity, 3, 3);
  rim.position.set(1.5, 4.5, -4.5);
  scene.add(key, fill, rim);

  // RectAreaLight cannot cast a shadow, so the contact shadow is a separate
  // directional light that contributes almost no colour of its own.
  const shadowLight = new DirectionalLight(0xffffff, 0.35);
  shadowLight.position.set(-2.5, 6, 3);
  shadowLight.castShadow = true;
  shadowLight.shadow.mapSize.set(cfg.shadowMapSize, cfg.shadowMapSize);
  shadowLight.shadow.radius = cfg.shadowSoftness;
  shadowLight.shadow.bias = -0.0012;
  const cam = shadowLight.shadow.camera;
  cam.left = -6; cam.right = 6; cam.top = 6; cam.bottom = -6; cam.near = 0.1; cam.far = 30;
  scene.add(shadowLight, shadowLight.target);

  const productMaterial = new MeshPhysicalMaterial({
    color: new Color(cfg.color),
    roughness: cfg.roughness,
    metalness: cfg.metalness,
    clearcoat: cfg.coat,
    clearcoatRoughness: 0.35,
    // Real uploads are open in ways fixtures never are; a culled hole reads as
    // a hole in the product rather than as one in the file.
    side: DoubleSide,
  });

  let backdropHex = cfg.backdrop ?? pickBackdrop(cfg.color, cfg.minContrast);
  const sweepMaterial = new MeshStandardMaterial({
    color: new Color(backdropHex), roughness: 0.85, metalness: 0, side: DoubleSide,
  });
  const sweep = new Mesh(cyclorama(), sweepMaterial);
  sweep.receiveShadow = true;
  scene.add(sweep);

  const depthMaterial = new ShaderMaterial({
    uniforms: { uNear: { value: 1 }, uFar: { value: 10 } },
    vertexShader: DEPTH_SHADER.vertex,
    fragmentShader: DEPTH_SHADER.fragment,
    side: DoubleSide,
  });
  const normalMaterial = new MeshNormalMaterial({ side: DoubleSide });

  // The product hangs off a pivot so a turntable rotates it, not the camera.
  const pivot = new Group();
  scene.add(pivot);
  let mesh: Mesh | null = null;
  /** The geometry as loaded, kept so a new orientation can be applied from scratch. */
  let pristine: BufferGeometry | null = null;
  let radius = 1;
  let centerY = 0;
  let currentYaw = 0;
  let pass: Pass = 'beauty';

  /*
    Frame once for the whole rotation, not for the angle you happen to be on.

    Re-fitting per frame makes the product breathe as it turns, and fitting at
    one yaw clips it at another. So the distance is solved against the widest
    projection across a full turn, and then never changes. That is also why the
    turntable can run without touching the camera again.

    The Blender scene fits the rotated bounding box, whose corners stick out
    well past an elongated object and leave it small in frame. Sampling real
    vertices instead frames what a viewer actually sees.
  */
  const YAW_SAMPLES = 16;
  const VERTEX_BUDGET = 800;

  function fitCamera() {
    if (!mesh) return;
    const elev = MathUtils.degToRad(cfg.elevation);
    const dir = new Vector3(0, Math.sin(elev), Math.cos(elev)).normalize();
    const focus = new Vector3(0, centerY, 0);
    const vertical = MathUtils.degToRad(camera.fov);
    const horizontal = 2 * Math.atan(Math.tan(vertical / 2) * camera.aspect);
    let d = (radius / Math.sin(Math.min(vertical, horizontal) / 2)) * 1.3;

    const pos = mesh.geometry.getAttribute('position');
    const step = Math.max(1, Math.floor(pos.count / VERTEX_BUDGET));
    const target = 1 - 2 * cfg.margin;
    const v = new Vector3();
    const held = pivot.rotation.y;

    const spreadAt = (distance: number): number => {
      camera.position.copy(focus).addScaledVector(dir, distance);
      camera.lookAt(focus);
      camera.near = Math.max(distance / 500, 0.01);
      camera.far = distance * 12;
      camera.updateProjectionMatrix();
      camera.updateMatrixWorld(true);

      let worst = 0;
      for (let k = 0; k < YAW_SAMPLES; k++) {
        pivot.rotation.y = (k / YAW_SAMPLES) * Math.PI * 2;
        pivot.updateMatrixWorld(true);
        for (let i = 0; i < pos.count; i += step) {
          v.fromBufferAttribute(pos, i).applyMatrix4(mesh!.matrixWorld).project(camera);
          worst = Math.max(worst, Math.abs(v.x), Math.abs(v.y));
        }
      }
      return worst;
    };

    for (let iter = 0; iter < 8; iter++) {
      const spread = spreadAt(d);
      if (Math.abs(spread - target) < 0.005) break;
      d *= spread / target;
    }
    spreadAt(d);                       // leave the camera at the solved distance
    pivot.rotation.y = held;           // and the turntable where it was
    pivot.updateMatrixWorld(true);

    shadowLight.target.position.copy(focus);
    shadowLight.target.updateMatrixWorld();
    // Spread the depth ramp across the product only, so the map uses its range.
    depthMaterial.uniforms.uNear.value = Math.max(d - radius * 1.2, 0.01);
    depthMaterial.uniforms.uFar.value = d + radius * 1.2;
  }

  let pinned: number | null = null;

  function resize() {
    const w = pinned ?? mount.clientWidth;
    const h = pinned ?? mount.clientHeight;
    if (!w || !h) return;
    // updateStyle false when pinned: the canvas keeps its CSS box while the
    // drawing buffer is exactly the size the frames must be.
    renderer.setSize(w, h, pinned === null ? false : false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    fitCamera();
  }

  function render() {
    scene.overrideMaterial = pass === 'depth' ? depthMaterial : pass === 'normal' ? normalMaterial : null;
    // Conditioning passes want raw values, not a photographic curve.
    renderer.toneMapping = pass === 'beauty' ? AgXToneMapping : 0;
    renderer.render(scene, camera);
    scene.overrideMaterial = null;
  }

  function show(geometry: BufferGeometry): StudioStats {
    if (mesh) {
      pivot.remove(mesh);
      mesh.geometry.dispose();
    }
    if (pristine !== geometry) {
      pristine?.dispose();
      pristine = geometry.clone();
    }
    geometry = pristine.clone();
    // Orientation first, in file axes, X then Y then Z: Blender's XYZ Euler.
    const [rx, ry, rz] = cfg.rotate;
    if (rx) geometry.rotateX(MathUtils.degToRad(rx));
    if (ry) geometry.rotateY(MathUtils.degToRad(ry));
    if (rz) geometry.rotateZ(MathUtils.degToRad(rz));
    const box = new Box3().setFromBufferAttribute(geometry.getAttribute('position') as never);
    const size = box.getSize(new Vector3());
    const center = box.getCenter(new Vector3());
    geometry.translate(-center.x, -center.y, -center.z);

    // Scale so the longest axis is a fixed size, exactly as the Blender scene
    // does, so lighting and lens read the same whatever the real millimetres.
    const longest = Math.max(size.x, size.y, size.z) || 1;
    const s = 2 / longest;
    geometry.scale(s, s, s);
    geometry.computeBoundingSphere();

    // A multicolour display.ply carries each part's filament colour per vertex; it replaces cfg.color.
    const painted = geometry.hasAttribute('color');
    if (productMaterial.vertexColors !== painted) {
      productMaterial.vertexColors = painted;
      productMaterial.needsUpdate = true;
    }
    productMaterial.color.set(painted ? '#ffffff' : cfg.color);

    mesh = new Mesh(geometry, productMaterial);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    // Slicers and CAD are Z-up, three is Y-up.
    mesh.rotation.x = -Math.PI / 2;
    mesh.updateMatrixWorld(true);

    // Stand it on the floor, so the contact shadow lands where the object does.
    const world = new Box3().setFromObject(mesh);
    mesh.position.y -= world.min.y;
    mesh.updateMatrixWorld(true);
    const standing = new Box3().setFromObject(mesh);
    centerY = (standing.min.y + standing.max.y) / 2;
    radius = standing.getSize(new Vector3()).length() / 2;

    pivot.add(mesh);
    resize();
    return { size, triangles: geometry.getAttribute('position').count / 3, backdrop: backdropHex };
  }

  function setConfig(patch: Partial<StudioConfig>) {
    const turned = patch.rotate !== undefined && patch.rotate.join() !== cfg.rotate.join();
    const remap = patch.shadowMapSize !== undefined && patch.shadowMapSize !== cfg.shadowMapSize;
    cfg = { ...cfg, ...patch };
    if (remap) {
      // three allocates the map at the size it finds on the next shadow pass
      shadowLight.shadow.mapSize.set(cfg.shadowMapSize, cfg.shadowMapSize);
      shadowLight.shadow.map?.dispose();
      shadowLight.shadow.map = null;
    }
    if (turned && pristine) show(pristine);
    productMaterial.color.set(productMaterial.vertexColors ? '#ffffff' : cfg.color);
    productMaterial.roughness = cfg.roughness;
    productMaterial.metalness = cfg.metalness;
    productMaterial.clearcoat = cfg.coat;
    backdropHex = cfg.backdrop ?? pickBackdrop(cfg.color, cfg.minContrast);
    sweepMaterial.color.set(backdropHex);
    key.intensity = cfg.keyIntensity;
    fill.intensity = cfg.fillIntensity;
    rim.intensity = cfg.rimIntensity;
    scene.environmentIntensity = cfg.environmentIntensity;
    renderer.shadowMap.enabled = cfg.shadow;
    shadowLight.visible = cfg.shadow;
    shadowLight.shadow.radius = cfg.shadowSoftness;
    shadowLight.intensity = cfg.shadow ? 0.35 : 0;
    renderer.toneMappingExposure = cfg.exposure;
    camera.fov = cfg.fov;
    camera.updateProjectionMatrix();
    fitCamera();
  }

  const observer = new ResizeObserver(resize);
  observer.observe(mount);
  resize();

  return {
    show,
    setConfig,
    config: () => ({ ...cfg }),
    setPass(next) { pass = next; },
    setYaw(deg) { currentYaw = deg; pivot.rotation.y = -MathUtils.degToRad(deg); },
    yaw: () => currentYaw,
    render,
    capture() { render(); return renderer.domElement.toDataURL('image/png'); },
    captureBlob() {
      render();
      return new Promise<Blob>((resolve, reject) =>
        renderer.domElement.toBlob(
          (b) => (b ? resolve(b) : reject(new Error('the canvas produced no image'))),
          'image/png',
        ),
      );
    },
    setSize(px) { pinned = px; resize(); },
    resize,
    dispose() {
      observer.disconnect();
      mesh?.geometry.dispose();
      pristine?.dispose();
      productMaterial.dispose();
      sweepMaterial.dispose();
      depthMaterial.dispose();
      normalMaterial.dispose();
      sweep.geometry.dispose();
      envRT.dispose();
      pmrem.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
