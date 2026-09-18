import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const backend = env.ERRGRIND_BACKEND || "http://127.0.0.1:8765";
  const proxy = {
    target: backend,
    changeOrigin: true,
    headers: { Origin: backend },
  };
  return {
    plugins: [react()],
    base: "/static/assistant-ui/",
    build: {
      outDir: "../errgrind/web/static/assistant-ui",
      emptyOutDir: true,
    },
    server: {
      port: 5173,
      proxy: {
        "/api": proxy,
        "/assistant-ui": proxy,
        "/static": proxy,
      },
    },
  };
});
