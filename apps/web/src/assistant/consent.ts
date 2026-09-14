import type { BusinessContext } from "./types";

// Consent covers data transmission, independently from permission to modify data.
export function consentKey(thread: string, profile: { id: string; version: number; base_url: string }, context: BusinessContext) {
  const keys: (keyof BusinessContext)[] = ["workspace", "table_id", "row_ids", "search", "view_id", "task_id", "template_id", "template_version", "table_ids", "task_ids", "template_ids"];
  const scope = Object.fromEntries(keys.flatMap(key => {
    const value = context[key];
    if (value == null || value === false || value === "") return [];
    return [[key, Array.isArray(value) ? [...new Set<string | number>(value)].sort() : value]];
  }));
  return JSON.stringify([thread, profile.id, profile.version, profile.base_url, scope]);
}
