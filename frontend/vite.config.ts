import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import type { ViteDevServer, PreviewServer } from 'vite';

// The production nginx image rewrites these two paths to local docs pages.
// Mirror that behavior in Vite development and preview (without touching /openapi.json).
function docsRoutes(server: ViteDevServer | PreviewServer) {
  server.middlewares.use((request, _response, next) => {
    if (request.url === '/docs' || request.url === '/docs/') request.url = '/docs.html';
    if (request.url === '/redoc' || request.url === '/redoc/') request.url = '/redoc.html';
    next();
  });
}

export default defineConfig({
  plugins: [react(), { name: 'offline-docs-routes', configureServer: docsRoutes, configurePreviewServer: docsRoutes }],
  server: {
    host: '0.0.0.0',
    allowedHosts: ['.e2b.app'],
    // The browser always uses relative /api URLs. Vite forwards them to a
    // locally running API during development; nginx does the same in Docker.
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/healthz': 'http://127.0.0.1:8000',
      '/readyz': 'http://127.0.0.1:8000',
    },
  },
  test: { environment: 'jsdom', globals: true },
});