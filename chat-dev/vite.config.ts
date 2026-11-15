import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  server: { port: 5199, fs: { allow: [path.resolve(__dirname, '..')] } },
  resolve: {
    dedupe: ['react', 'react-dom'],
    alias: {
      react: path.resolve(__dirname, 'node_modules/react'),
      'react-dom': path.resolve(__dirname, 'node_modules/react-dom'),
    },
  },
  test: { include: ['../web/src/panels/chat/**/*.test.ts', 'src/**/*.test.ts'], environment: 'node' },
});
