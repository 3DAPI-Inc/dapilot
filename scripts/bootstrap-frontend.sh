#!/usr/bin/env bash
#
# Scaffold dapilot's frontend: React + TS + Vite + Tailwind 4 + shadcn.
#
# Deliberately NOT pinned to 3dapi/frontend. Different product, different
# audience, and inheriting that repo's versions would start this one a year
# behind for the sake of component paste-ability we are unlikely to use.
# Same libraries, because those are the right choices and the team knows them.
set -euo pipefail

DAPILOT="${DAPILOT:-$HOME/Desktop/work/dapilot}"
FE="$DAPILOT/frontend"

[ -d "$DAPILOT/.git" ] || { echo "not a git repo: $DAPILOT" >&2; exit 1; }
[ -e "$FE" ]           && { echo "$FE exists, refusing" >&2; exit 1; }

echo "==> node $(node -v)"

# -- 1. Vite scaffold --------------------------------------------------
# create-vite >=9 resolves the target relative to cwd and strips a leading
# slash, so cd into the temp dir and hand it a bare name.
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
( cd "$TMP" && npm create vite@latest app -- --template react-ts )
[ -d "$TMP/app" ] || { echo "vite scaffold produced nothing at $TMP/app" >&2; exit 1; }
mkdir -p "$FE"
rsync -a --exclude node_modules "$TMP/app/" "$FE/"
cd "$FE"
npm install

# -- 2. Path alias, before shadcn init (it validates one exists) -------
# No baseUrl: TS 5.4+ resolves paths relative to the tsconfig, and TS 6
# errors on baseUrl as deprecated.
node -e '
const fs = require("fs");
for (const f of ["tsconfig.json", "tsconfig.app.json"]) {
  if (!fs.existsSync(f)) continue;
  let s = fs.readFileSync(f, "utf8");          // these ship with comments, so no JSON.parse
  if (s.includes("\"paths\"")) continue;
  if (/"compilerOptions"\s*:\s*\{/.test(s)) {
    s = s.replace(/"compilerOptions"\s*:\s*\{/,
      `"compilerOptions": {\n    "paths": { "@/*": ["./src/*"] },`);
  } else {
    s = s.replace(/^\{/, `{\n  "compilerOptions": { "paths": { "@/*": ["./src/*"] } },`);
  }
  fs.writeFileSync(f, s);
}
'

# -- 3. Tailwind 4 -----------------------------------------------------
# Must exist before shadcn init, which refuses to run without it. Tailwind 4
# is a Vite plugin, not a postcss step, and has no tailwind.config.js.
npm install tailwindcss @tailwindcss/vite
printf '@import "tailwindcss";\n' > src/index.css

cat > vite.config.ts <<'EOF'
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'path';

export default defineConfig({
  server: {
    port: Number(process.env.VITE_DEV_PORT) || 8081,   // 8080 is dapi's
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY || 'http://localhost:3100',
        changeOrigin: true,
      },
    },
  },
  plugins: [react(), tailwindcss()],
  // import.meta.dirname, not __dirname: Vite's native config loader rejects the latter.
  resolve: { alias: { '@': path.resolve(import.meta.dirname, './src') } },
});
EOF

# -- 4. shadcn ---------------------------------------------------------
# NOT `-d`: that flag means --template=next and would scaffold a Next app.
# -b is the component library (radix|base|aria), -p the preset. Base color
# defaults to neutral, so dapilot gets its own identity rather than dapi's.
npx --yes shadcn@latest init -b radix -p nova -y --css-variables </dev/null
npx --yes shadcn@latest add -y button card input badge dialog table progress </dev/null

# -- 5. Dependencies ---------------------------------------------------
npm install react-router-dom @tanstack/react-query \
            react-hook-form @hookform/resolvers zod \
            lucide-react sonner date-fns
npm install -D @types/node

# -- 6. App skeleton ---------------------------------------------------
mkdir -p src/api src/hooks src/pages
rm -f src/App.css src/assets/react.svg
node -e '
const fs = require("fs");
const p = JSON.parse(fs.readFileSync("package.json", "utf8"));
p.name = "dapilot-frontend";
fs.writeFileSync("package.json", JSON.stringify(p, null, 2) + "\n");
'

cat > .env.example <<'EOF'
VITE_API_URL=/api
VITE_API_PROXY=http://localhost:3100
VITE_DEV_PORT=8081
EOF

cat > src/main.tsx <<'EOF'
import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { Toaster } from 'sonner';
import App from './App';
import './index.css';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
        <Toaster richColors position="top-right" />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
EOF

cat > src/App.tsx <<'EOF'
import { Routes, Route, Navigate } from 'react-router-dom';
import Upload from './pages/Upload';
import Job from './pages/Job';
import Review from './pages/Review';
import Catalog from './pages/Catalog';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/upload" replace />} />
      <Route path="/upload" element={<Upload />} />
      <Route path="/jobs/:jobId" element={<Job />} />
      <Route path="/jobs/:jobId/review" element={<Review />} />
      <Route path="/catalog" element={<Catalog />} />
    </Routes>
  );
}
EOF

cat > src/api/client.ts <<'EOF'
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
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!res.ok) throw new ApiError(res.status, (await res.text()) || res.statusText);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}
EOF

cat > src/api/jobs.ts <<'EOF'
import { api } from './client';

// Mirrors the stage registry in protocol/pipeline.md. Keep in sync.
export type JobStage =
  | 'intake' | 'analyze' | 'slice' | 'price'
  | 'render' | 'research' | 'copy' | 'publish';

export type JobStatus = 'queued' | 'running' | 'blocked' | 'done' | 'failed';

export interface Job {
  id: string;
  fileName: string;
  stage: JobStage;
  status: JobStatus;
  createdAt: string;
  updatedAt: string;
  /** Set when status is 'blocked' or 'failed'. */
  reason?: string;
}

export interface JobEvent {
  stage: JobStage;
  attempt: number;
  status: JobStatus;
  durationMs: number;
  at: string;
}

export const listJobs     = ()           => api<Job[]>('/jobs');
export const getJob       = (id: string) => api<Job>(`/jobs/${id}`);
export const getJobEvents = (id: string) => api<JobEvent[]>(`/jobs/${id}/events`);
export const approveJob   = (id: string) => api<Job>(`/jobs/${id}/approve`, { method: 'POST' });
EOF

for p in Upload Job Review Catalog; do
  cat > "src/pages/$p.tsx" <<EOF
export default function $p() {
  return <div className="p-8 text-2xl font-semibold">$p</div>;
}
EOF
done

# -- 7. Verify ---------------------------------------------------------
npm run build   # tsc then vite build; fails loudly if the alias is wrong

echo "==> done.  cd $FE && npm run dev   -> http://localhost:8081"
