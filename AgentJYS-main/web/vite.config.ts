import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const proxyTarget = env.VITE_AETHER_PROXY_TARGET || "http://localhost:8080";
  const p4ProxyTarget = env.VITE_P4_PROXY_TARGET || "http://localhost:8090";

  return {
    plugins: [react()],
    cacheDir: env.AETHER_WEB_CACHE_DIR || "node_modules/.vite",
    build: { outDir: env.AETHER_WEB_BUILD_DIR || "dist" },
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/p3": {
          target: proxyTarget,
          changeOrigin: true,
        },
        "/p4-api": {
          target: p4ProxyTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/p4-api/, ""),
        },
        "/api": {
          target: proxyTarget,
          changeOrigin: true,
        },
        "/health": {
          target: proxyTarget,
          changeOrigin: true,
        },
      },
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: "./src/test/setup.ts",
    },
  };
});
