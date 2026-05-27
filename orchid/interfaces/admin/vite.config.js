import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/admin/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5175,
    proxy: {
      '/api': 'http://localhost:7842',
      '/ws': {
        target: 'ws://localhost:7842',
        ws: true,
      },
    },
  },
})
