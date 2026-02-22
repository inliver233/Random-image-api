import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = (env.VITE_API_PROXY_TARGET || "http://localhost:8000").trim();
  const buildMinifyRaw = (process.env.VITE_BUILD_MINIFY || env.VITE_BUILD_MINIFY || "").trim().toLowerCase();
  const buildMinify = buildMinifyRaw ? !["0", "false", "no", "off"].includes(buildMinifyRaw) : true;

  return {
    base: mode === "production" ? "/admin/" : "/",
    plugins: [react()],
    server: {
      proxy: {
        "/admin/api": apiTarget,
        "/metrics": apiTarget,
      },
    },
    build: {
      minify: buildMinify ? "esbuild" : false,
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      pool: "threads",
      poolOptions: { threads: { singleThread: true } },
    },
  };
});
