import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

const health = { status: "ok", api_version: "v1" };
const runtime = { components: [{ component_kind: "text", model_identifier: "local-text", state: "unloaded", error_code: null, explicitly_warmed: false }] };

function jsonResponse(value: unknown, status = 200): Response {
  return Response.json(value, { status });
}

describe("workbench overview", () => {
  it("shows liveness and disconnected state without inventing records", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(health));
    vi.stubGlobal("fetch", fetcher);
    render(<App />);

    expect(await screen.findByText("API process is responding")).toBeInTheDocument();
    expect(screen.getByText("Connect to view jobs")).toBeInTheDocument();
    expect(screen.getByText("Connect to view artifacts")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0]?.[0]).toBe("/health");
    expect(screen.queryByText(/benchmark example/i)).not.toBeInTheDocument();
  });

  it("verifies a token, displays real empty data, and clears the connection on disconnect", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/health") return jsonResponse(health);
      if (path === "/api/v1/runtime/models") return jsonResponse(runtime);
      if (path.startsWith("/api/v1/jobs")) return jsonResponse({ jobs: [] });
      if (path.startsWith("/api/v1/artifacts")) return jsonResponse({ artifacts: [] });
      return jsonResponse({ error: { code: "not_found" } }, 404);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("Bearer token"), "memory-only-token");
    await user.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(await screen.findByText("No jobs registered")).toBeInTheDocument();
    expect(screen.getByText("No result artifacts")).toBeInTheDocument();
    expect(screen.getByText("local-text")).toBeInTheDocument();
    expect(fetcher.mock.calls.some(([, init]) => new Headers(init?.headers).get("Authorization") === "Bearer memory-only-token")).toBe(true);
    expect(fetcher.mock.calls.every(([input]) => !String(input).includes("memory-only-token"))).toBe(true);

    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(screen.getByText("Connect to view jobs")).toBeInTheDocument();
    expect(screen.getByLabelText("Bearer token")).toHaveValue("");
  });

  it("treats a rejected bearer token as an authentication failure", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      if (String(input) === "/health") return jsonResponse(health);
      return jsonResponse({ error: { code: "authentication_required" } }, 401);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText("Bearer token"), "invalid-secret");
    await user.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Authentication failed");
    expect(screen.queryByText("Connected")).not.toBeInTheDocument();
    expect(screen.queryByText("invalid-secret")).not.toBeInTheDocument();
  });

  it("shows an actionable API error and only calls supported routes", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/health") return jsonResponse(health);
      if (path === "/api/v1/runtime/models") return jsonResponse(runtime);
      if (path.startsWith("/api/v1/jobs")) return jsonResponse({ error: { code: "catalog_unavailable" } }, 503);
      return jsonResponse({ artifacts: [] });
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Bearer token"), "valid-token");
    await user.click(screen.getByRole("button", { name: "Connect" }));

    expect((await screen.findAllByText("The API request failed (catalog_unavailable).", { exact: true })).length).toBe(2);
    await waitFor(() => expect(fetcher.mock.calls.length).toBeGreaterThan(3));
    const called = fetcher.mock.calls.map(([input]) => String(input));
    expect(called.some((path) => /documents|query|indexes/.test(path))).toBe(false);
  });
});
