import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const root = path.dirname(fileURLToPath(import.meta.url));

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const envDir = path.resolve(root, '..');
  const env = loadEnv(mode, envDir, 'COPILOT_');
  const requestedPort = Number(env.COPILOT_RUNTIME_PORT);
  const copilotPort =
    Number.isSafeInteger(requestedPort) &&
    requestedPort > 0 &&
    requestedPort <= 65_535
      ? requestedPort
      : 4000;

  return {
    envDir,
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(root, './src'),
      },
    },
    server: {
      port: 5173,
      proxy: {
        '/api/copilotkit': {
          target: `http://127.0.0.1:${copilotPort}`,
          changeOrigin: false,
        },
      },
    },
  };
});
