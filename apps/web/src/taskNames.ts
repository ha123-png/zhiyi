import type { Task } from "./types";

export function taskDisplayName(task: Pick<Task, "filename" | "file_name">): string {
  return task.file_name?.confirmed_filename || task.file_name?.suggested_filename || task.filename;
}

export function taskTemplateName(
  task: Pick<Task, "template_id" | "template_mode" | "candidate_templates">,
  currentName?: string,
): string {
  if (!task.template_id) return task.template_mode === "smart" ? "智能匹配" : "未指定";
  return currentName
    ?? task.candidate_templates.find(template => template.id === task.template_id)?.name
    ?? "模板名称待确认";
}
