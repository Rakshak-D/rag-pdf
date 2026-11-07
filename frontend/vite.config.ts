import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { createBootstrapProxyConfigurationError } from "./src/viteProxy.ts";

const apiTarget = process.env.RAG_WORKBENCH_DEV_PROXY_TARGET ?? "http://127.0.0.1:8000";
const bootstrapTargetValue = process.env.RAG_WORKBENCH_BOOTSTRAP_DEV_PROXY_TARGET?.trim();
const gatewayTargetValue = process.env.RAG_WORKBENCH_LOCAL_GATEWAY_TARGET?.trim();
const gatewayFlag = process.env.VITE_RAG_LOCAL_GATEWAY;
if (gatewayFlag !== undefined && gatewayFlag !== "true" && gatewayFlag !== "false") {
  throw new Error("VITE_RAG_LOCAL_GATEWAY must be true or false");
}
const localGateway = gatewayFlag === "true";

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
const gatewayTarget = gatewayTargetValue ? loopbackTarget(gatewayTargetValue) : undefined;
if (localGateway && !gatewayTarget) {
  throw new Error("RAG_WORKBENCH_LOCAL_GATEWAY_TARGET must be a loopback HTTP origin when local gateway mode is enabled");
}

const bootstrapProxyConfigurationError = createBootstrapProxyConfigurationError({
  bootstrapTarget,
  gatewayTarget,
  localGateway,
});

export default defineConfig({
  // Worktrees may share installed dependencies, but must not share optimizer state.
  cacheDir: ".vite-cache",
  plugins: [react(), bootstrapProxyConfigurationError],
  server: {
    proxy: {
      "/health": { target: localGateway ? gatewayTarget! : apiTarget, changeOrigin: false },
      "/api": { target: localGateway ? gatewayTarget! : apiTarget, changeOrigin: false },
      ...(localGateway ? { "/bootstrap-api": {
        target: gatewayTarget!,
        changeOrigin: false,
      } } : bootstrapTarget ? { "/bootstrap-api": {
        target: bootstrapTarget,
        changeOrigin: false,
        rewrite: (path: string) => path === "/bootstrap-api/health" ? "/health" : path.replace(/^\/bootstrap-api/, "/api"),
      } } : {}),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    clearMocks: true,
    restoreMocks: true,
  },
});
