import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      // 前端统一走相对路径，由 Vite 代理到本地 FastAPI
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
