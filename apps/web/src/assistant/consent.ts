import type { BusinessContext } from "./types";

type ConsentProfile = { id: string; version: number; base_url: string; provider?: string; model_name?: string };

// Keep the service path: gateways can route different paths to different tenants.
// Refuse credential-bearing URLs instead of ever copying them into browser storage.
function serviceAddress(value: string): string | null {
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) return null;
    return `${url.origin}${url.pathname.replace(/\/+$/, "")}`;
  } catch { return null; }
}

export function serviceConsentKey(profile: ConsentProfile): string {
  const address = serviceAddress(profile.base_url);
  return address && profile.provider && profile.model_name
    ? JSON.stringify([profile.id, profile.provider, address, profile.model_name]) : "";
}

export function serviceConsentLabel(profile: ConsentProfile): string {
  return serviceAddress(profile.base_url) || "地址无效的模型服务";
}

export function serviceConsentStorageKey(apiBase: string): string {
  return `zhiyi:remote-service-consent:v1:${apiBase}`;
}

export interface ServiceConsentState {
  keys: string[];
  epoch: string;
  revoked: Record<string, string>;
}

const emptyState = (): ServiceConsentState => ({ keys: [], epoch: "", revoked: {} });

function validServiceKey(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    const parts: unknown = JSON.parse(value);
    return Array.isArray(parts) && parts.length === 4 && parts.every(part => typeof part === "string" && part.length > 0)
      && serviceAddress(parts[2]) === parts[2];
  } catch { return false; }
}

function parseState(saved: unknown): ServiceConsentState {
  // Read the first implementation's explicit remembered choices as well.
  if (Array.isArray(saved)) return { keys: [...new Set(saved.filter(validServiceKey))].slice(-100), epoch: "legacy", revoked: {} };
  if (!saved || typeof saved !== "object") return emptyState();
  const record = saved as Partial<ServiceConsentState>;
  if (!Array.isArray(record.keys) || typeof record.epoch !== "string" || !record.epoch || !record.revoked || typeof record.revoked !== "object") return emptyState();
  return {
    keys: [...new Set(record.keys.filter(validServiceKey))].slice(-100),
    epoch: record.epoch,
    revoked: Object.fromEntries(Object.entries(record.revoked).filter(([id, token]) => id && typeof token === "string" && token)),
  };
}

export function readServiceConsentState(storageKey: string): ServiceConsentState {
  try {
    const raw = localStorage.getItem(storageKey);
    // A failed durable revocation can still survive a reload in this window.
    // Ignore that fallback after a later successful change to localStorage.
    const fallback = JSON.parse(sessionStorage.getItem(`${storageKey}:revoked`) || "null") as { basis?: string | null; state?: unknown } | null;
    if (fallback && fallback.basis === raw) return parseState(fallback.state);
    return raw === null ? emptyState() : parseState(JSON.parse(raw));
  } catch { return emptyState(); }
}

export function sessionConsentRevision(state: ServiceConsentState, profileId: string): string {
  return state.epoch ? `${state.epoch}:${state.revoked[profileId] || ""}` : "";
}

export function saveServiceConsent(storageKey: string, key: string, allowed: boolean, remember = true): {
  saved: boolean; keys: string[]; state: ServiceConsentState; sessionSaved?: boolean;
} {
  const previous = readServiceConsentState(storageKey);
  if (!validServiceKey(key)) return { saved: false, keys: previous.keys, state: previous };
  const profileId = serviceProfileId(key)!;
  const keys = allowed && !remember ? previous.keys
    : [...previous.keys.filter(value => value !== key), ...(allowed ? [key] : [])].slice(-100);
  const state: ServiceConsentState = {
    keys,
    epoch: previous.epoch || crypto.randomUUID(),
    revoked: allowed ? previous.revoked : { ...previous.revoked, [profileId]: crypto.randomUUID() },
  };
  try {
    localStorage.setItem(storageKey, JSON.stringify(state));
    return { saved: true, keys, state };
  } catch {
    if (allowed) return { saved: false, keys: previous.keys, state: previous };
    // Quota failures should not leave an old allow record behind. Clearing
    // remembered choices also invalidates old session grants via their epoch.
    try {
      localStorage.removeItem(storageKey);
      const cleared = emptyState();
      return { saved: true, keys: cleared.keys, state: cleared };
    } catch { /* Some environments permit replacement but reject removal. */ }
    try {
      localStorage.setItem(storageKey, "null");
      const cleared = emptyState();
      return { saved: true, keys: cleared.keys, state: cleared };
    } catch { /* Keep a per-window revocation if durable storage is blocked. */ }
    try {
      sessionStorage.setItem(`${storageKey}:revoked`, JSON.stringify({ basis: localStorage.getItem(storageKey), state }));
      return { saved: false, keys, state, sessionSaved: true };
    } catch { return { saved: false, keys, state, sessionSaved: false }; }
  }
}

export function serviceProfileId(key: string): string | null {
  if (!validServiceKey(key)) return null;
  return (JSON.parse(key) as string[])[0];
}

// Also removes legacy session grants, which used the same profile-id position.
export function revokeProfileGrants(grants: string[], profileId: string): string[] {
  return grants.filter(key => {
    try { return (JSON.parse(key) as unknown[])[1] !== profileId; }
    catch { return false; }
  });
}

// Consent covers data transmission, independently from permission to modify data.
export function consentKey(thread: string, profile: ConsentProfile, context: BusinessContext, revision = "") {
  const address = serviceAddress(profile.base_url);
  if (!address) return "";
  const keys: (keyof BusinessContext)[] = ["workspace", "table_id", "row_ids", "source_keys", "search", "view_id", "task_id", "template_id", "template_version", "table_ids", "task_ids", "template_ids"];
  const scope = Object.fromEntries(keys.flatMap(key => {
    const value = context[key];
    if (value == null || value === false || value === "") return [];
    return [[key, Array.isArray(value) ? [...new Set<string | number>(value)].sort() : value]];
  }));
  return JSON.stringify([thread, profile.id, profile.version, address, scope, profile.provider || "", profile.model_name || "", revision]);
}
