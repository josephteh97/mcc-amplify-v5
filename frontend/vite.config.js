import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// /api  → FastAPI HTTP routes
// /api/ws/* — proxied as a WebSocket; ws:true is required
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});
