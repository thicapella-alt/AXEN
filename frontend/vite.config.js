import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          vendor:   ['react', 'react-dom', 'react-router-dom'],
          recharts: ['recharts'],
        },
      },
    },
  },
  server: {
    port: 3000,
    proxy: {
      // All /api/* requests in dev are forwarded to the FastAPI backend.
      // This eliminates CORS issues during local development — the browser
      // always talks to port 3000; Vite proxies to port 8000 transparently.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
