import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

// HUB 前端：本地开发把 /hub/v1 代理到 gateway 8091；
// 构建产物输出到 ./dist 给 Docker 多阶段构建拷到 backend/static。
export default defineConfig({
  plugins: [vue(), tailwindcss()],
  server: {
    port: 5174,
    proxy: {
      '/hub/v1': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: './dist',
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks: {
          'vendor-vue': ['vue', 'vue-router', 'pinia'],
          'vendor-charts': ['chart.js'],
          'vendor-icons': ['lucide-vue-next'],
          'vendor-http': ['axios'],
        },
      },
    },
  },
  esbuild: {
    drop: process.env.NODE_ENV === 'production' ? ['console', 'debugger'] : [],
  },
})
