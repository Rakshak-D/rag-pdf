import type { Page, Response } from "@playwright/test";

export const BOOTSTRAP_STATUS_PATH = "/bootstrap-api/v1/bootstrap/status";

export const BOOTSTRAP_STATUS_EVENTS = [
  "input_filled",
  "submit_attempted",
  "submit_click_completed",
  "submit_click_failed",
  "browser_request_observed",
  "browser_request_non_get",
  "route_handler_entered",
  "route_role_invalid",
  "route_role_bootstrap",
  "route_role_ordinary",
  "route_role_none",
  "route_role_other",
  "route_allowed",
  "route_rejected",
  "request_failed",
  "http_response_401",
  "http_response_success",
  "http_response_other",
  "response_wait_registered",
  "response_wait_matched",
  "response_wait_timed_out",
  "response_wait_failed",
  "ui_authentication_error_observed",
  "browser_page_error_observed",
] as const;

export type BootstrapStatusEvent = typeof BOOTSTRAP_STATUS_EVENTS[number];
export interface BootstrapStatusWitness { events: BootstrapStatusEvent[]; }

const allowedEvents = new Set<string>(BOOTSTRAP_STATUS_EVENTS);
const MAX_EVENTS = 32;

export function createBootstrapStatusWitness(): BootstrapStatusWitness {
  return { events: [] };
}

export function recordBootstrapStatusEvent(witness: BootstrapStatusWitness, event: unknown): void {
  if (witness.events.length >= MAX_EVENTS || typeof event !== "string" || !allowedEvents.has(event)) return;
  witness.events.push(event as BootstrapStatusEvent);
}

function responseEvent(status: number): BootstrapStatusEvent {
  if (status === 401) return "http_response_401";
  if (status >= 200 && status < 300) return "http_response_success";
  return "http_response_other";
}

export function recordBootstrapStatusResponse(witness: BootstrapStatusWitness, status: number): void {
  recordBootstrapStatusEvent(witness, responseEvent(status));
}

function isTimeout(error: unknown): boolean {
  return error instanceof Error && error.name === "TimeoutError";
}

/** Register the fixed status-response waiter before triggering the submit action. */
export function waitForBootstrapStatusResponse(
  page: Pick<Page, "waitForResponse">,
  submit: () => Promise<unknown>,
  witness: BootstrapStatusWitness,
): Promise<Response> {
  recordBootstrapStatusEvent(witness, "response_wait_registered");

  let pendingResponse: Promise<Response>;
  try {
    pendingResponse = page.waitForResponse((response) => {
      const request = response.request();
      return new URL(response.url()).pathname === BOOTSTRAP_STATUS_PATH && request.method() === "GET";
    });
  } catch (error) {
    recordBootstrapStatusEvent(witness, "response_wait_failed");
    return Promise.reject(error);
  }

  const response = pendingResponse.then((value) => {
    recordBootstrapStatusEvent(witness, "response_wait_matched");
    return value;
  }, (error: unknown) => {
    recordBootstrapStatusEvent(witness, isTimeout(error) ? "response_wait_timed_out" : "response_wait_failed");
    throw error;
  });

  const action = Promise.resolve().then(submit).then(() => {
    recordBootstrapStatusEvent(witness, "submit_click_completed");
  }, (error: unknown) => {
    recordBootstrapStatusEvent(witness, "submit_click_failed");
    throw error;
  });

  return Promise.all([response, action]).then(([matched]) => matched);
}
