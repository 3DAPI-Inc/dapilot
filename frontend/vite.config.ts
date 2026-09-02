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
