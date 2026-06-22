import { defineConfig } from 'vite';

export default defineConfig({
  base: process.env.NODE_ENV === 'production' ? '/static/' : '/',
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:9900',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:9900',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: '../static',
    emptyOutDir: true,
    assetsDir: 'assets',
    sourcemap: false,
  },
});
