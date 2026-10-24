import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In development the browser talks to this server, and /api is forwarded to the FastAPI backend, so no CORS
// setup is needed locally. In production set VITE_API_URL to the hosted API (and add this site to CORS_ORIGINS).
const api = process.env.API_PROXY_TARGET || 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': api } },
  preview: { proxy: { '/api': api } },
});
