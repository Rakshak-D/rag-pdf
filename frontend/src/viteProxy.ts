import type { Plugin } from "vite";

interface BootstrapProxyOptions {
  bootstrapTarget?: string | undefined;
  gatewayTarget?: string | undefined;
  localGateway: boolean;
}

export function createBootstrapProxyConfigurationError(options: BootstrapProxyOptions): Plugin {
  return {
    name: "bootstrap-proxy-configuration",
    configureServer(server) {
      if (options.bootstrapTarget || (options.localGateway && options.gatewayTarget)) return;
      server.middlewares.use("/bootstrap-api", (_request, response) => {
        response.statusCode = 503;
        response.setHeader("Content-Type", "application/json; charset=utf-8");
        response.setHeader("Cache-Control", "no-store");
        response.end(JSON.stringify({ error: { code: "bootstrap_proxy_unconfigured" } }));
      });
    },
  };
}
