import type { ChatModelCatalog } from "../../api/types";

export const GENERAL_CHAT_PREFERENCES_KEY = "rag-pdf.general-chat-preferences";
const MODEL_ID = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;

export interface GeneralChatPreferences {
  selectedModel: string | null;
  temperature: number;
  maxOutputTokens: number;
}

export type PreferenceLoadStatus = "loaded" | "missing" | "invalid" | "unavailable" | "saved";

export interface LoadedGeneralChatPreferences {
  preferences: GeneralChatPreferences;
  status: PreferenceLoadStatus;
}

export const DEFAULT_GENERAL_CHAT_PREFERENCES: Readonly<GeneralChatPreferences> = Object.freeze({
  selectedModel: null,
  temperature: 0.7,
  maxOutputTokens: 512,
});

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function browserStorage(): StorageLike | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isValidGeneralChatPreferences(value: unknown): value is GeneralChatPreferences {
  if (!isRecord(value)) return false;
  const keys = Object.keys(value).sort();
  if (keys.join(",") !== "maxOutputTokens,selectedModel,temperature") return false;
  return (value.selectedModel === null || (typeof value.selectedModel === "string" && MODEL_ID.test(value.selectedModel))) &&
    typeof value.temperature === "number" && Number.isFinite(value.temperature) &&
    value.temperature >= 0 && value.temperature <= 2 &&
    typeof value.maxOutputTokens === "number" && Number.isInteger(value.maxOutputTokens) &&
    value.maxOutputTokens >= 32 && value.maxOutputTokens <= 2048;
}

export function parseGeneralChatPreferences(raw: string | null): LoadedGeneralChatPreferences {
  if (raw === null) return { preferences: { ...DEFAULT_GENERAL_CHAT_PREFERENCES }, status: "missing" };
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed) || Object.keys(parsed).sort().join(",") !== "maxOutputTokens,selectedModel,temperature,version" ||
      parsed.version !== 1 || !isValidGeneralChatPreferences({
        selectedModel: parsed.selectedModel,
        temperature: parsed.temperature,
        maxOutputTokens: parsed.maxOutputTokens,
      })) {
      return { preferences: { ...DEFAULT_GENERAL_CHAT_PREFERENCES }, status: "invalid" };
    }
    return {
      preferences: {
        selectedModel: parsed.selectedModel as string | null,
        temperature: parsed.temperature as number,
        maxOutputTokens: parsed.maxOutputTokens as number,
      },
      status: "loaded",
    };
  } catch {
    return { preferences: { ...DEFAULT_GENERAL_CHAT_PREFERENCES }, status: "invalid" };
  }
}

export function loadGeneralChatPreferences(storage: StorageLike | null = browserStorage()): LoadedGeneralChatPreferences {
  if (!storage) return { preferences: { ...DEFAULT_GENERAL_CHAT_PREFERENCES }, status: "unavailable" };
  try {
    const result = parseGeneralChatPreferences(storage.getItem(GENERAL_CHAT_PREFERENCES_KEY));
    if (result.status === "invalid") storage.removeItem(GENERAL_CHAT_PREFERENCES_KEY);
    return result;
  } catch {
    return { preferences: { ...DEFAULT_GENERAL_CHAT_PREFERENCES }, status: "unavailable" };
  }
}

export function saveGeneralChatPreferences(
  preferences: GeneralChatPreferences,
  storage: StorageLike | null = browserStorage(),
): boolean {
  if (!storage || !isValidGeneralChatPreferences(preferences)) return false;
  try {
    storage.setItem(GENERAL_CHAT_PREFERENCES_KEY, JSON.stringify({ version: 1, ...preferences }));
    return true;
  } catch {
    return false;
  }
}

export type ChatModelResolution =
  | { model: string | null; status: "checking" | "service-unavailable" }
  | { model: string; status: "selected" | "configured-default" }
  | { model: string | null; status: "saved-model-unavailable" | "configured-default-unavailable"; unavailableModel?: string };

export function resolveChatModel(
  preferences: GeneralChatPreferences,
  catalog: ChatModelCatalog | null,
): ChatModelResolution {
  if (!catalog) return { model: preferences.selectedModel, status: "checking" };
  if (catalog.availability === "unavailable") return { model: null, status: "service-unavailable" };
  if (preferences.selectedModel && catalog.available_models.includes(preferences.selectedModel)) {
    return { model: preferences.selectedModel, status: "selected" };
  }
  if (preferences.selectedModel) {
    return catalog.configured_model_available
      ? { model: catalog.configured_model, status: "saved-model-unavailable", unavailableModel: preferences.selectedModel }
      : { model: null, status: "saved-model-unavailable", unavailableModel: preferences.selectedModel };
  }
  return catalog.configured_model_available
    ? { model: catalog.configured_model, status: "configured-default" }
    : { model: null, status: "configured-default-unavailable" };
}
