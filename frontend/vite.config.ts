import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import type { Plugin } from "vite";

const apiTarget = process.env.RAG_WORKBENCH_DEV_PROXY_TARGET ?? "http://127.0.0.1:8000";
const bootstrapTargetValue = process.env.RAG_WORKBENCH_BOOTSTRAP_DEV_PROXY_TARGET?.trim();

function loopbackTarget(value: string): string {
  let target: URL;
  try { target = new URL(value); } catch { throw new Error("RAG_WORKBENCH_BOOTSTRAP_DEV_PROXY_TARGET must be a loopback HTTP URL"); }
  const host = target.hostname.replace(/^\[|\]$/g, "").toLowerCase();
  const loopback = host === "localhost" || host === "::1" || /^127(?:\.\d{1,3}){3}$/.test(host);
  if (!loopback || !["http:", "https:"].includes(target.protocol) || target.username || target.password || target.pathname !== "/" || target.search || target.hash) {
    throw new Error("RAG_WORKBENCH_BOOTSTRAP_DEV_PROXY_TARGET must be a loopback HTTP origin without credentials or a path");
  }
  return target.origin;
}

const bootstrapTarget = bootstrapTargetValue ? loopbackTarget(bootstrapTargetValue) : undefined;

const bootstrapProxyConfigurationError: Plugin = {
  name: "bootstrap-proxy-configuration",
  configureServer(server) {
    if (bootstrapTarget) return;
    server.middlewares.use("/bootstrap-api", (_request, response) => {
      response.statusCode = 503;
      response.setHeader("Content-Type", "application/json; charset=utf-8");
      response.setHeader("Cache-Control", "no-store");
      response.end(JSON.stringify({ error: { code: "bootstrap_proxy_unconfigured" } }));
    });
  },
};

export default defineConfig({
  plugins: [react(), bootstrapProxyConfigurationError],
  server: {
    proxy: {
      "/health": { target: apiTarget, changeOrigin: false },
      "/api": { target: apiTarget, changeOrigin: false },
      ...(bootstrapTarget ? { "/bootstrap-api": {
        target: bootstrapTarget,
        changeOrigin: false,
        rewrite: (path: string) => path === "/bootstrap-api/health" ? "/health" : path.replace(/^\/bootstrap-api/, "/api"),
      } } : {}),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    clearMocks: true,
    restoreMocks: true,
  },
});
