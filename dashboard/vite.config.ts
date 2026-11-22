import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Backend FastAPI local (ver backend/README.md).
const BACKEND = 'http://127.0.0.1:8000';

// Por defecto solo escucha en localhost. Para exponerlo en la red usa: npm run dev -- --host
export default defineConfig({
  plugins: [react()],
  test: {
    // Ignora archivos AppleDouble (._*) que no son código fuente.
    exclude: ['**/node_modules/**', '**/dist/**', '**/._*'],
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    // El navegador solo habla con /api; Vite lo reenvía al backend FastAPI local.
    proxy: { '/api': { target: BACKEND, changeOrigin: true } },
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
    strictPort: true,
    proxy: { '/api': { target: BACKEND, changeOrigin: true } },
  },
});
