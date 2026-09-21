import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const proxyTarget = env.VITE_AETHER_PROXY_TARGET || "http://localhost:8080";
  const p4ProxyTarget = env.VITE_P4_PROXY_TARGET || "http://localhost:8090";

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
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
