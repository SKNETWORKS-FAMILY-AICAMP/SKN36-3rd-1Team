import { writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import react from '../../frontend/node_modules/@vitejs/plugin-react/dist/index.mjs';
import { build } from '../../frontend/node_modules/vite/dist/node/index.js';

const output = await build({
  configFile: false,
  root: fileURLToPath(new URL('../../frontend/', import.meta.url)),
  envDir: false,
  define: {
    'import.meta.env.VITE_ASK_API_URL': JSON.stringify(''),
    'import.meta.env.VITE_POLICY_COMPARISON_PREVIEW': JSON.stringify('./ssok-saved-policy-compare.html'),
  },
  plugins: [react()],
  build: {
    write: false,
    modulePreload: false,
    rollupOptions: { output: { inlineDynamicImports: true } },
  },
});

const assets = output.output;
const html = assets.find(asset => asset.fileName === 'index.html').source;
const css = assets.find(asset => asset.fileName.endsWith('.css'));
const app = assets.find(asset => asset.type === 'chunk' && asset.isEntry).code;
const offlineHtml = html
  .replace(/<script type="module"[^>]*src="[^"]+"[^>]*><\/script>/, '')
  .replace(new RegExp('<link[^>]*href="[^"]*' + css.fileName + '"[^>]*>'), () => '<style>' + css.source + '</style>')
  .replace('</body>', () =>
    '<script type="module">' + app.replace(/<\/script/gi, '<\\/script') + '</script></body>');
await writeFile(new URL('./ssok-full-preview.html', import.meta.url), offlineHtml, 'utf8');
