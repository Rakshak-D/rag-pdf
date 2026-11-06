import { describe, expect, it, vi } from "vitest";
import type { Page, Response } from "@playwright/test";
import {
  BOOTSTRAP_STATUS_PATH,
  createBootstrapStatusWitness,
  recordBootstrapStatusEvent,
  recordBootstrapStatusResponse,
  waitForBootstrapStatusResponse,
} from "../../e2e/bootstrap-status-witness";

function response(status: number): Response {
  return {
    url: () => `http://127.0.0.1:4173${BOOTSTRAP_STATUS_PATH}`,
    request: () => ({ method: () => "GET" }),
    status: () => status,
  } as unknown as Response;
}

describe("bootstrap status request witness", () => {
  it("registers the exact GET response waiter before the submit action", async () => {
    const witness = createBootstrapStatusWitness();
    let registered = false;
    const page = {
      waitForResponse: vi.fn((predicate: (value: Response) => boolean) => {
        registered = true;
        expect(predicate(response(401))).toBe(true);
        return Promise.resolve(response(401));
      }),
    } as unknown as Pick<Page, "waitForResponse">;
    const submit = vi.fn(async () => {
      expect(registered).toBe(true);
    });

    await expect(waitForBootstrapStatusResponse(page, submit, witness)).resolves.toBeDefined();
    expect(submit).toHaveBeenCalledOnce();
    expect(witness.events).toEqual(expect.arrayContaining([
      "response_wait_registered",
      "response_wait_matched",
      "submit_click_completed",
    ]));
    expect(witness.events).not.toContain("http_response_401");
  });

  it("distinguishes an HTTP response from the response-wait predicate", async () => {
    const witness = createBootstrapStatusWitness();
    recordBootstrapStatusResponse(witness, 401);
    expect(witness.events).toEqual(["http_response_401"]);
  });

  it("records timeout and request-wait failure separately without retaining error text", async () => {
    for (const [name, expectedEvent] of [
      ["TimeoutError", "response_wait_timed_out"],
      ["TypeError", "response_wait_failed"],
    ] as const) {
      const witness = createBootstrapStatusWitness();
      const failure = new Error("Bearer synthetic-secret C:\\private\\token.txt");
      failure.name = name;
      const page = {
        waitForResponse: vi.fn(() => Promise.reject(failure)),
      } as unknown as Pick<Page, "waitForResponse">;

      await expect(waitForBootstrapStatusResponse(page, async () => undefined, witness)).rejects.toBe(failure);
      expect(witness.events).toContain(expectedEvent);
      expect(JSON.stringify(witness)).not.toContain("synthetic-secret");
      expect(JSON.stringify(witness)).not.toContain("private");
    }
  });

  it("accepts only fixed event names and caps the in-memory witness", () => {
    const witness = createBootstrapStatusWitness();
    recordBootstrapStatusEvent(witness, "input_filled");
    recordBootstrapStatusEvent(witness, "Bearer synthetic-secret");
    for (let index = 0; index < 40; index += 1) recordBootstrapStatusEvent(witness, "browser_request_observed");
    expect(witness.events).toHaveLength(32);
    expect(witness.events).not.toContain("Bearer synthetic-secret");
  });
});
