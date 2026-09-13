import { useCallback, useEffect, useRef, useState } from 'react';
import { captureFromQuery, captureTurntable } from '@/lib/model/capture';
import { loadPreview } from '@/lib/model/load';
import { STUDIO_DEFAULTS, createStudio, type Pass, type Studio as StudioScene, type StudioConfig, type StudioStats } from '@/lib/model/studio';
import { ConsoleNav, SiteHeader } from '@/components/site/SiteHeader';
import { Spinner } from '@/components/site/Spinner';
import { cn } from '@/lib/utils';

/*
  The studio bench.

  Somewhere to point a real browser at the product scene and turn every knob
  while watching it move. The scene itself lives in lib/model/studio.ts and
  knows nothing about this page, so the headless capture script can drive the
  same code with no DOM beyond a canvas.
*/

const PASSES: { id: Pass; label: string; note: string }[] = [
  { id: 'beauty', label: 'Beauty', note: 'the photograph' },
  { id: 'depth', label: 'Depth', note: 'near is white; conditioning map' },
  { id: 'normal', label: 'Normal', note: 'surface direction as colour' },
];

/** Every knob, grouped the way you would reach for them. */
const SLIDERS: { key: keyof StudioConfig; label: string; min: number; max: number; step: number }[] = [
  { key: 'keyIntensity', label: 'key', min: 0, max: 12, step: 0.1 },
  { key: 'fillIntensity', label: 'fill', min: 0, max: 12, step: 0.1 },
  { key: 'rimIntensity', label: 'rim', min: 0, max: 12, step: 0.1 },
  { key: 'environmentIntensity', label: 'ambient', min: 0, max: 2, step: 0.01 },
  { key: 'exposure', label: 'exposure', min: 0.2, max: 3, step: 0.01 },
  { key: 'roughness', label: 'roughness', min: 0, max: 1, step: 0.01 },
  { key: 'coat', label: 'coat', min: 0, max: 1, step: 0.01 },
  { key: 'shadowOpacity', label: 'shadow', min: 0, max: 1, step: 0.01 },
  { key: 'shadowSoftness', label: 'softness', min: 0, max: 12, step: 0.1 },
  { key: 'fov', label: 'lens (fov)', min: 12, max: 70, step: 1 },
  { key: 'elevation', label: 'elevation', min: -10, max: 70, step: 1 },
  { key: 'margin', label: 'margin', min: 0, max: 0.35, step: 0.01 },
];

const label = 'font-sans text-[0.6875rem] uppercase tracking-[0.22em] text-slate/60';

export default function Studio() {
  const mount = useRef<HTMLDivElement>(null);
  const studio = useRef<StudioScene | null>(null);
  const [cfg, setCfg] = useState<StudioConfig>(() => {
    // The query string may set the look, so a headless capture can ask for a
    // colour without anyone clicking the swatch.
    const q = new URLSearchParams(window.location.search);
    const colour = q.get('color');
    const backdrop = q.get('backdrop');
    const rotate = (q.get('rotate') ?? '').split(',').map(Number);
    const elevation = Number(q.get('elevation'));
    return {
      ...STUDIO_DEFAULTS,
      ...(rotate.length === 3 && rotate.every(Number.isFinite) ? { rotate: rotate as [number, number, number] } : {}),
      ...(q.has('elevation') && Number.isFinite(elevation) ? { elevation } : {}),
      ...(colour ? { color: colour.startsWith('#') ? colour : `#${colour}` } : {}),
      ...(backdrop ? { backdrop: backdrop.startsWith('#') ? backdrop : `#${backdrop}` } : {}),
    };
  });
  /*
    The query string is the scene's other control surface. The page reads it on
    mount so a headless browser, which cannot click anything, can ask for one
    pass at one angle and screenshot the result.
  */
  const params = new URLSearchParams(window.location.search);
  const asked = params.get('pass');
  const [pass, setPass] = useState<Pass>(
    asked === 'depth' || asked === 'normal' ? asked : 'beauty',
  );
  const [yaw, setYaw] = useState(Number(params.get('yaw') ?? 35));
  const [spin, setSpin] = useState(params.get('spin') !== '0' && !params.has('yaw'));
  const [stats, setStats] = useState<StudioStats | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!mount.current) return;
    const scene = createStudio(mount.current);
    studio.current = scene;
    return () => { scene.dispose(); studio.current = null; };
  }, []);

  // One loop owns the clock. Spinning advances yaw; otherwise it redraws
  // whatever the controls last set.
  useEffect(() => {
    let frame = 0;
    let last = performance.now();
    const tick = (now: number) => {
      frame = requestAnimationFrame(tick);
      const scene = studio.current;
      if (!scene) return;
      if (spin) {
        const next = (scene.yaw() + ((now - last) / 1000) * 24) % 360;
        scene.setYaw(next);
        setYaw(next);
      }
      last = now;
      scene.render();
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [spin]);

  useEffect(() => { studio.current?.setConfig(cfg); }, [cfg]);
  useEffect(() => { studio.current?.setPass(pass); }, [pass]);
  useEffect(() => { if (!spin) studio.current?.setYaw(yaw); }, [yaw, spin]);

  const open = useCallback(async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      const preview = await loadPreview(file, new AbortController().signal);
      if (preview.kind !== 'geometry') throw new Error('this file only carries a plate render, no geometry');
      setStats(studio.current?.show(preview.geometry) ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  /*
    ?model=<url> loads a file without the picker. The capture script needs this
    because a headless browser cannot use a file input, and in dev it also
    reaches anything on disk through Vite's /@fs/ prefix.
  */
  useEffect(() => {
    const url = new URLSearchParams(window.location.search).get('model');
    if (!url) return;
    let cancelled = false;
    (async () => {
      setBusy(true);
      try {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`${res.status} fetching the model`);
        const blob = await res.blob();
        if (cancelled) return;
        const name = decodeURIComponent(url.split('/').pop() || 'model.stl');
        await open(new File([blob], name));
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();
    return () => { cancelled = true; };
  }, [open]);

  /*
    Capture mode. The driver opens this page with capture=1 and a collector URL,
    waits for the status line below to say done, then encodes what it received.
    The status goes in the title as well as the DOM because a headless browser
    is easiest to poll through the page title.
  */
  const [capture, setCapture] = useState<string>('idle');
  const started = useRef(false);
  useEffect(() => {
    const options = captureFromQuery(window.location.search);
    if (!options || !stats || started.current) return;   // wait for a model
    started.current = true;
    setSpin(false);
    (async () => {
      try {
        setCapture('capturing');
        document.title = 'capture: capturing';
        await captureTurntable(studio.current!, options, ({ done, total }) => {
          const line = `capture: ${done}/${total}`;
          setCapture(line);
          document.title = line;
        });
        setCapture('done');
        document.title = 'capture: done';
      } catch (e) {
        const why = e instanceof Error ? e.message : String(e);
        setCapture(`failed: ${why}`);
        document.title = `capture: failed: ${why}`;
      }
    })();
  }, [stats]);

  const save = () => {
    const url = studio.current?.capture();
    if (!url) return;
    const a = document.createElement('a');
    a.href = url;
    a.download = `studio_${pass}_${Math.round(yaw)}.png`;
    a.click();
  };

  const set = (patch: Partial<StudioConfig>) => setCfg((c) => ({ ...c, ...patch }));

  return (
    <div className="min-h-svh bg-paper text-navy">
      <SiteHeader><ConsoleNav /></SiteHeader>
      <main className="mx-auto max-w-[1180px] px-5 pb-24 sm:px-10">
        <h1 className="font-display text-4xl font-semibold tracking-tight">Studio</h1>
        <p className="mt-3 max-w-[58ch] font-sans leading-relaxed text-slate">
          The product scene, live. Open a model, turn the knobs, and watch what the render
          stage would produce. The depth and normal passes are the maps a diffusion model
          conditions on, and they come off the same scene at no extra cost.
        </p>

        <div className="mt-8 grid gap-8 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <div>
            <div
              ref={mount}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) open(f); }}
              className="aspect-square w-full overflow-hidden rounded-lg border border-mist bg-mist/30"
            />
            <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-3">
              <label className="cursor-pointer font-sans text-sm text-navy underline underline-offset-4">
                Open a model
                <input
                  type="file"
                  accept=".stl,.3mf,.obj"
                  className="hidden"
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) open(f); }}
                />
              </label>
              <button type="button" onClick={() => setSpin((s) => !s)} className="font-sans text-sm text-navy underline underline-offset-4">
                {spin ? 'Stop the turntable' : 'Turn it'}
              </button>
              <button type="button" onClick={save} className="font-sans text-sm text-navy underline underline-offset-4">
                Save this frame
              </button>
              {busy ? <span className="flex items-center gap-2 font-sans text-sm text-slate"><Spinner className="size-4" />Reading the mesh.</span> : null}
              {error ? <span className="font-sans text-sm text-destructive">{error}</span> : null}
              {capture !== 'idle' ? (
                <span data-capture={capture} className="font-sans text-sm tabular-nums text-navy">{capture}</span>
              ) : null}
            </div>

            <div className="mt-4 flex flex-wrap gap-2">
              {PASSES.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => setPass(p.id)}
                  title={p.note}
                  className={cn(
                    'rounded-md border px-3 py-1.5 font-sans text-sm transition-colors',
                    pass === p.id ? 'border-navy bg-navy text-paper' : 'border-mist text-slate hover:text-navy',
                  )}
                >
                  {p.label}
                </button>
              ))}
              <span className="ml-auto self-center font-sans text-sm tabular-nums text-slate">
                yaw {Math.round(yaw)}&deg;
              </span>
            </div>

            {stats ? (
              <p className="mt-3 font-sans text-sm text-slate">
                {stats.size.x.toFixed(1)} &times; {stats.size.y.toFixed(1)} &times; {stats.size.z.toFixed(1)} mm ·{' '}
                {stats.triangles.toLocaleString()} triangles · backdrop {stats.backdrop}
              </p>
            ) : null}
          </div>

          <aside className="space-y-5">
            <div>
              <span className={label}>Filament</span>
              <div className="mt-2 flex items-center gap-3">
                <input
                  type="color"
                  value={cfg.color}
                  onChange={(e) => set({ color: e.target.value })}
                  className="size-8 cursor-pointer rounded border border-mist bg-transparent"
                />
                <span className="font-sans text-sm tabular-nums text-slate">{cfg.color}</span>
              </div>
            </div>

            <div>
              <span className={label}>Backdrop</span>
              <div className="mt-2 flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => set({ backdrop: null })}
                  className={cn('font-sans text-sm underline underline-offset-4', cfg.backdrop === null ? 'text-navy' : 'text-slate')}
                >
                  auto
                </button>
                <input
                  type="color"
                  value={cfg.backdrop ?? '#F2F0EB'}
                  onChange={(e) => set({ backdrop: e.target.value })}
                  className="size-8 cursor-pointer rounded border border-mist bg-transparent"
                />
                <span className="font-sans text-sm tabular-nums text-slate">
                  {cfg.backdrop ?? stats?.backdrop ?? 'derived'}
                </span>
              </div>
            </div>

            <label className="flex items-center gap-2 font-sans text-sm text-slate">
              <input type="checkbox" checked={cfg.shadow} onChange={(e) => set({ shadow: e.target.checked })} />
              contact shadow
            </label>

            {SLIDERS.map(({ key, label: name, min, max, step }) => (
              <label key={key} className="block">
                <span className="flex items-baseline justify-between font-sans text-sm text-slate">
                  {name}
                  <span className="tabular-nums text-navy">{(cfg[key] as number).toFixed(2)}</span>
                </span>
                <input
                  type="range"
                  min={min}
                  max={max}
                  step={step}
                  value={cfg[key] as number}
                  onChange={(e) => set({ [key]: Number(e.target.value) } as Partial<StudioConfig>)}
                  className="mt-1 w-full accent-navy"
                />
              </label>
            ))}

            <button
              type="button"
              onClick={() => setCfg(STUDIO_DEFAULTS)}
              className="font-sans text-sm text-navy underline underline-offset-4"
            >
              Back to defaults
            </button>
          </aside>
        </div>
      </main>
    </div>
  );
}
