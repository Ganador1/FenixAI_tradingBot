import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tsconfigPaths from "vite-tsconfig-paths";

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_TARGET || 'http://localhost:8000'
  const socketTarget = apiTarget.startsWith('http')
    ? apiTarget.replace(/^http/, 'ws')
    : apiTarget

  return {
  plugins: [
    react(),
    tsconfigPaths(),
  ],
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        secure: true,
      },
      '/socket.io': {
        target: socketTarget,
        ws: true,
        changeOrigin: true,
      }
    }
  }
}
})
