import { defineConfig } from 'vite'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { crx } from '@crxjs/vite-plugin'
import manifest from './public/manifest.json'

const __dirname = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig({
  plugins: [
    react(),
    crx({ manifest }),
  ],
  build: {
    rollupOptions: {
      // CRXJS gère popup + content + background via le manifest. Les pages
      // additionnelles (tracker) doivent être déclarées en input Rollup
      // pour que leur <script src="./index.tsx"> soit bundlé (sinon le
      // HTML est copié tel quel et le navigateur tente de charger du TSX
      // brut → erreur).
      input: {
        tracker: resolve(__dirname, 'src/tracker/index.html'),
      },
    },
  },
  server: {
    host: 'localhost',
    port: 5173,
    strictPort: true,
    cors: {
      origin: [/chrome-extension:\/\//],
    },
  },
})
