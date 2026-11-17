/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, fileURLToPath(new URL("..", import.meta.url)), "");
  const proxy = {
    "/api": {
      target: env.DASHBOARD_API_TARGET || "http://127.0.0.1:8001",
      changeOrigin: true,
      rewrite: (path: string) => path.replace(/^\/api/, ""),
      configure(
        server: import("vite").ProxyOptions extends never ? never : any,
      ) {
        server.on("proxyReq", (req: any, incoming: any) => {
          // Secrets stay in this loopback Node process, never in browser bundles.
          const path: string = incoming.url || "";
          req.removeHeader("authorization");
          if (
            path.startsWith("/dashboard/") ||
            path.startsWith("/api/dashboard/")
          )
            req.setHeader(
              "Authorization",
              `Bearer ${env.DASHBOARD_TOKEN || ""}`,
            );
          if (
            path === "/telephony/mock/incoming" ||
            path === "/api/telephony/mock/incoming"
          )
            req.setHeader(
              "Authorization",
              `Bearer ${env.TELEPHONY_MONITOR_TOKEN || ""}`,
            );
        });
      },
    },
  };
  return {
    plugins: [react()],
    server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy },
    preview: { host: "127.0.0.1", port: 5173, strictPort: true, proxy },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/tests/setup.ts"],
      css: false,
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            react: ["react", "react-dom", "react-router-dom"],
            mui: ["@mui/material", "@emotion/react", "@emotion/styled"],
            query: ["@tanstack/react-query", "axios"],
          },
        },
      },
    },
  };
});
