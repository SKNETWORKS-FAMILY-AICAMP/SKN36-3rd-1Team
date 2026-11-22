import { defineConfig } from '../../frontend/node_modules/vite/dist/node/index.js';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../../frontend/', import.meta.url));
export default defineConfig({ root, envDir: fileURLToPath(new URL('./', import.meta.url)),
  define: { 'import.meta.env.VITE_ASK_API_URL': JSON.stringify('') },
  server: { host: '127.0.0.1', port: 5177, strictPort: true,
    fs: { allow: [root, fileURLToPath(new URL('../../data/releases/v1.4.0-candidate/', import.meta.url))] } } });
