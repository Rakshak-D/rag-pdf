import { describe, expect, it, vi } from "vitest";
import { ApiError, BootstrapApiClient, WorkbenchApiClient } from "./client";

const models = {
  availability: "available",
  configured_model: "gemma4:e2b",
  configured_model_available: true,
  available_models: ["gemma4:e2b"],
  error_code: null,
};

describe("local gateway and general chat clients", () => {
  it("keeps backend bearer credentials out of ordinary and bootstrap browser requests in gateway mode", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      if (String(input) === "/api/v1/runtime/models") return Response.json({ components: [] });
      return Response.json({ mode: "bootstrap", status: "running", accepting_jobs: true, worker_count: 1, queue_capacity: 2 });
    });
    await new WorkbenchApiClient({ token: "internal-marker", fetcher, localGateway: true }).getRuntimeModels();
    await new BootstrapApiClient({ token: "internal-marker", fetcher, localGateway: true }).getStatus();
    expect(fetcher.mock.calls.every(([, init]) => !new Headers(init?.headers).has("Authorization"))).toBe(true);
  });

  it("sends validated session history to General Chat and validates the response", async () => {
    const response = { mode: "general", answer: "Hello from local chat.", model: "gemma4:e2b" };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json(models))
      .mockResolvedValueOnce(Response.json(response));
    const client = new WorkbenchApiClient({ token: null, fetcher, localGateway: true });
    await expect(client.chatModels()).resolves.toEqual(models);
    await expect(client.generalChat({ question: "Follow up", history: [{ user: "Earlier", assistant: "Earlier reply" }] })).resolves.toEqual(response);
    const [url, init] = fetcher.mock.calls[1] ?? [];
    expect(url).toBe("/api/v1/chat");
    expect(JSON.parse(String(init?.body))).toEqual({ question: "Follow up", history: [{ user: "Earlier", assistant: "Earlier reply" }] });
    expect(new Headers(init?.headers).has("Authorization")).toBe(false);
  });

  it("sends supported generation preferences as dedicated fields without raw Ollama options", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ mode: "general", answer: "Configured reply.", model: "qwen2.5:3b" }));
    const client = new WorkbenchApiClient({ token: null, fetcher, localGateway: true });
    await client.generalChat({
      question: "Explain embeddings",
      history: [],
      model: "qwen2.5:3b",
      generation: { temperature: 0.4, max_output_tokens: 768 },
    });
    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body))).toEqual({
      question: "Explain embeddings",
      history: [],
      model: "qwen2.5:3b",
      generation: { temperature: 0.4, max_output_tokens: 768 },
    });
  });

  it("preserves bearer mode for independent development and rejects malformed chat responses", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ mode: "unknown", answer: "hi", model: "x" }));
    const client = new WorkbenchApiClient({ token: "dev-token", fetcher, localGateway: false });
    await expect(client.generalChat({ question: "Hello", history: [] })).rejects.toBeInstanceOf(ApiError);
    expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe("Bearer dev-token");
  });
});
