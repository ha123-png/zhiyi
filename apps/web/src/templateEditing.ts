import type { TemplateDraft, TemplateField } from "./types";

// Convert legacy understanding hints only in the editor. Stored snapshots remain intact.
export function mergeTemplateHints(text: string, hints: string[]): string {
  const parts = [text];
  const existing = new Set(text.split("\n").map((line) => line.trim()));
  for (const hint of hints) {
    if (!hint.trim() || existing.has(hint.trim()) || (`\n${text.trim()}\n`).includes(`\n${hint.trim()}\n`)) continue;
    parts.push(hint);
    existing.add(hint.trim());
  }
  return parts.filter(Boolean).join("\n");
}

export function editableTemplate(draft: TemplateDraft): TemplateDraft {
  return { ...draft, extra_instructions: mergeTemplateHints(draft.extra_instructions, draft.validation_rules), validation_rules: [] };
}

export function moveTemplateField<T extends TemplateField>(fields: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= fields.length || to >= fields.length) return fields;
  const next = [...fields];
  const [field] = next.splice(from, 1);
  next.splice(to, 0, field);
  return next;
}
