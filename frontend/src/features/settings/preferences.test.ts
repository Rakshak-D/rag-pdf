import { afterEach, describe, expect, it } from "vitest";
import type { ChatModelCatalog } from "../../api/types";
import {
  DEFAULT_GENERAL_CHAT_PREFERENCES,
  GENERAL_CHAT_PREFERENCES_KEY,
  loadGeneralChatPreferences,
  parseGeneralChatPreferences,
  resolveChatModel,
  saveGeneralChatPreferences,
} from "./preferences";

afterEach(() => window.localStorage.clear());

describe("General Chat preferences", () => {
  it("saves a versioned browser-local record and reloads it", () => {
    const preferences = { selectedModel: "qwen2.5:3b", temperature: 0.4, maxOutputTokens: 768 };
    expect(saveGeneralChatPreferences(preferences)).toBe(true);
    expect(JSON.parse(window.localStorage.getItem(GENERAL_CHAT_PREFERENCES_KEY) ?? "null")).toEqual({
      version: 1,
      ...preferences,
    });
    expect(loadGeneralChatPreferences()).toEqual({ preferences, status: "loaded" });
    expect(window.localStorage.getItem(GENERAL_CHAT_PREFERENCES_KEY)).not.toContain("token");
  });

  it("resets malformed or unsupported records to safe defaults", () => {
    expect(parseGeneralChatPreferences("not-json").status).toBe("invalid");
    for (const raw of [
      "not-json",
      JSON.stringify({ version: 2, selectedModel: null, temperature: 0.7, maxOutputTokens: 512 }),
      JSON.stringify({ version: 1, selectedModel: "qwen2.5:3b", temperature: 8, maxOutputTokens: 512 }),
      JSON.stringify({ version: 1, selectedModel: null, temperature: 0.7, maxOutputTokens: 512, system: "override" }),
    ]) {
      window.localStorage.setItem(GENERAL_CHAT_PREFERENCES_KEY, raw);
      expect(loadGeneralChatPreferences()).toEqual({
        preferences: { ...DEFAULT_GENERAL_CHAT_PREFERENCES }, status: "invalid",
      });
      expect(window.localStorage.getItem(GENERAL_CHAT_PREFERENCES_KEY)).toBeNull();
    }
  });

  it("falls back from a model absent from the installed catalog without substituting silently", () => {
    const catalog: ChatModelCatalog = {
      availability: "available",
      configured_model: "gemma4:e2b",
      configured_model_available: true,
      available_models: ["gemma4:e2b"],
      error_code: null,
    };
    expect(resolveChatModel({ ...DEFAULT_GENERAL_CHAT_PREFERENCES, selectedModel: "old-model:latest" }, catalog)).toEqual({
      model: "gemma4:e2b",
      status: "saved-model-unavailable",
      unavailableModel: "old-model:latest",
    });
  });

  it("distinguishes an unavailable service from an installed-model mismatch", () => {
    const unavailable: ChatModelCatalog = {
      availability: "unavailable",
      configured_model: "gemma4:e2b",
      configured_model_available: false,
      available_models: [],
      error_code: "ollama_unavailable",
    };
    expect(resolveChatModel(DEFAULT_GENERAL_CHAT_PREFERENCES, unavailable)).toEqual({
      model: null, status: "service-unavailable",
    });
  });
});
