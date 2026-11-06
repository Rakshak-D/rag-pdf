import { describe, expect, it, vi } from "vitest";
import { createBootstrapProxyConfigurationError } from "./viteProxy";

describe("Vite bootstrap proxy guard", () => {
  it("does not install the unconfigured response when the local gateway owns the proxy", () => {
    const plugin = createBootstrapProxyConfigurationError({
      localGateway: true,
      gatewayTarget: "http://127.0.0.1:8020",
    });
    const use = vi.fn();
    const configure = plugin.configureServer;
    const handler = typeof configure === "function" ? configure : configure?.handler;
    handler?.call({} as never, { middlewares: { use } } as never);
    expect(use).not.toHaveBeenCalled();
  });

  it("keeps the strict failure middleware when neither bootstrap route has a target", () => {
    const plugin = createBootstrapProxyConfigurationError({ localGateway: false });
    const use = vi.fn();
    const configure = plugin.configureServer;
    const handler = typeof configure === "function" ? configure : configure?.handler;
    handler?.call({} as never, { middlewares: { use } } as never);
    expect(use).toHaveBeenCalledOnce();
    expect(use.mock.calls[0]?.[0]).toBe("/bootstrap-api");
  });
});
