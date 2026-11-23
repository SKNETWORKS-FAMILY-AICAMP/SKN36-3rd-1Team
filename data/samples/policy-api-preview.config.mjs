import { defineConfig } from '../../frontend/node_modules/vite/dist/node/index.js';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../../frontend/', import.meta.url));
export default defineConfig({ root, envDir: fileURLToPath(new URL('./', import.meta.url)),
  define: { 'import.meta.env.VITE_ASK_API_URL': JSON.stringify('http://127.0.0.1:8178/api/ask'), 'import.meta.env.VITE_POLICY_API_URL': JSON.stringify('http://127.0.0.1:8178/api/policies') },
  server: { host: '127.0.0.1', port: 5178, strictPort: true,
    fs: { allow: [root, fileURLToPath(new URL('../../data/releases/v1.4.0-candidate/', import.meta.url))] } } });
