import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // every /api call is forwarded to FastAPI, so the frontend code never
      // hardcodes a host and there are no CORS surprises in development
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
