import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig(({ mode }) => ({ plugins: [vue()], base: mode === 'demo' ? './' : '/web/', server: { proxy: { '/api': 'http://127.0.0.1:8090' } } }))
