import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { spawn, type ChildProcess } from 'node:child_process';
import { existsSync } from 'node:fs';
import net from 'node:net';
import path from 'path';

const ROOT = path.resolve(import.meta.dirname, '..');
const API_PORT = Number(process.env.VITE_API_PORT) || 3100;
const API_TARGET = process.env.VITE_API_PROXY || `http://localhost:${API_PORT}`;

function portOpen(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const s = net.connect({ port, host: '127.0.0.1' });
    s.once('connect', () => { s.destroy(); resolve(true); });
    s.once('error', () => resolve(false));
  });
}

/**
 * The job API (server/app.py) is the console's backend. Rather than a second
 * thing to start, the dev server starts it when nothing answers on the port
 * and stops it with itself. An API already running (scripts/dev/up.sh, or one
 * started by hand) is left alone. Requests to /api wait for the port to answer,
 * so the first upload after `npm run dev` does not hit a 502 while uvicorn boots.
 */
function jobApi(): Plugin {
  let child: ChildProcess | null = null;
  let ready: Promise<boolean> | null = null;
  const py = process.env.DAPILOT_PYTHON || path.join(ROOT, '.venv', 'bin', 'python');

  const waitForPort = async (timeoutMs: number) => {
    const until = Date.now() + timeoutMs;
    while (Date.now() < until) {
      if (await portOpen(API_PORT)) return true;
      if (child && child.exitCode !== null) return false;
      await new Promise((r) => setTimeout(r, 250));
    }
    return false;
  };

  return {
    name: 'dapilot-job-api',
    apply: 'serve',
    async configureServer(server) {
      if (process.env.VITE_API_PROXY) return;   // the API lives elsewhere; nothing to start
      if (await portOpen(API_PORT)) {
        server.config.logger.info(`[job-api] already answering on :${API_PORT}; not starting one`);
        return;
      }
      if (!existsSync(py)) {
        server.config.logger.warn(`[job-api] no python at ${py}; start the API by hand or set DAPILOT_PYTHON`);
        return;
      }
      child = spawn(py, ['-m', 'uvicorn', 'server.app:app', '--port', String(API_PORT)], {
        cwd: ROOT, stdio: ['ignore', 'inherit', 'inherit'], env: { ...process.env, DAPILOT_PROJECT_DIR: ROOT },
      });
      child.on('exit', (code) => server.config.logger.warn(`[job-api] exited with ${code}`));
      const stop = () => { if (child && child.exitCode === null) child.kill('SIGTERM'); };
      server.httpServer?.once('close', stop);
      process.once('exit', stop);
      for (const sig of ['SIGINT', 'SIGTERM'] as const) process.once(sig, () => { stop(); process.exit(); });
      ready = waitForPort(20_000);
      server.config.logger.info(`[job-api] starting uvicorn on :${API_PORT}`);
      // Runs before the proxy: hold /api requests until the API answers.
      server.middlewares.use('/api', async (_req, res, next) => {
        if (ready && !(await ready)) {
          res.statusCode = 503;
          res.end('the job API did not start; see the dev server log');
          return;
        }
        next();
      });
    },
  };
}

export default defineConfig({
  server: {
    port: Number(process.env.VITE_DEV_PORT) || 8081,   // 8080 is dapi's
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
    // The studio page loads a model straight from work/<hash>/ through Vite's
    // /@fs/ prefix, which is refused unless the repo root is allowed. Dev only:
    // `vite build` never serves anything.
    fs: { allow: [ROOT] },
  },
  plugins: [jobApi(), react(), tailwindcss()],
  // import.meta.dirname, not __dirname: Vite's native config loader rejects the latter.
  resolve: { alias: { '@': path.resolve(import.meta.dirname, './src') } },
});
