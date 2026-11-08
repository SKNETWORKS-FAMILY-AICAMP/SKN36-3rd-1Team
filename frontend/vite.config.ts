import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: '127.0.0.1',
    fs: {
      // 정책 관계 지도는 저장소의 팀 기준 데이터(src/graph/loadGraph.ts와 같은 폴더)를 읽는다.
      // 개발 서버가 열 수 있는 범위를 frontend와 이 데이터 폴더로만 넓힌다(.env 등 다른 파일은 열지 않는다).
      allow: ['.', '../data/releases/v1.4.0-candidate'],
    },
  },
});
