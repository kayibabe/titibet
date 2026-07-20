import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import fs from 'fs'
import path from 'path'

/**
 * swVersion — injects a unique build timestamp into dist/sw.js.
 *
 * public/sw.js uses the placeholder string '__SW_VERSION__'.
 * After Vite copies public/ to dist/, this plugin overwrites dist/sw.js
 * with the placeholder replaced by 'titibet-<epoch-ms>'.
 *
 * Effect: every `npm run build` auto-bumps the service-worker cache version,
 * so browsers always pick up the new assets without a hard refresh.
 * No manual CACHE_VERSION bumping is ever needed again.
 */
function swVersion() {
  return {
    name: 'sw-version',
    apply: 'build',          // production builds only — dev uses the unregister path
    enforce: 'post',
    closeBundle() {
      const swPath = path.resolve(__dirname, 'dist', 'sw.js')
      if (!fs.existsSync(swPath)) return
      const version = `titibet-${Date.now()}`
      const content = fs.readFileSync(swPath, 'utf8')
      // Match the quoted JS string literal: const CACHE_VERSION = '__SW_VERSION__'
      const updated = content.replace("'__SW_VERSION__'", `'${version}'`)
      if (updated === content) {
        console.warn('[sw-version] WARNING: placeholder not found in dist/sw.js')
      } else {
        fs.writeFileSync(swPath, updated)
        console.log(`[sw-version] CACHE_VERSION → ${version}`)
      }
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), swVersion()],
  build: {
    rollupOptions: {
      output: {
        // Rolldown (Vite 8) requires manualChunks as a function
        manualChunks(id) {
          if (id.includes('node_modules/react/') || id.includes('node_modules/react-dom/')) {
            return 'vendor-react'
          }
          if (id.includes('node_modules/recharts/')) {
            return 'vendor-charts'
          }
          if (id.includes('node_modules/lucide-react/')) {
            return 'vendor-icons'
          }
        },
      },
    },
  },
  server: {
    // Prevent the browser from caching dev-server assets between sessions.
    // Without this, a normal page reload (not Ctrl+Shift+R) serves stale
    // JS module files from disk cache — showing old layouts/rules.
    headers: {
      'Cache-Control': 'no-store',
    },
    proxy: {
      '/api': 'http://localhost:8010',
      '/health': 'http://localhost:8010',
    },
  },
})
