import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 构建产物入库（web/dist），由 FastAPI 同源托管，运行期不需要 Node。
// 开发期 npm run dev 起 5173，/api 代理到本地服务。
//
// **文件名不带 content hash**。默认带 hash 是为了浏览器缓存失效，但 Rollup 的 hash 是
// **传递**的：一个 chunk 的 hash 含它依赖的 chunk 的 hash，改一行 App.vue 就顺着 import
// 图级联到全部 —— 实测改 4 个 .vue 文件，63 个产物里 57 个换了名字，git 眼里全是新文件，
// 一次前端小改往 .git 里塞 6MB。这是本地单人、自己的 FastAPI 托管的应用，缓存失效靠
// 响应头就够（server/app.py 给 /assets 发 Cache-Control: no-cache），不必靠文件名。
export default defineConfig({
  plugins: [vue()],
  base: './',
  build: {
    outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        entryFileNames: 'assets/[name].js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: 'assets/[name][extname]',
      },
    },
  },
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true } },
  },
})
