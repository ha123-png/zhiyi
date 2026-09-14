import type { Task } from "./types";

export function taskDisplayName(task: Pick<Task, "filename" | "file_name">): string {
  return task.file_name?.confirmed_filename || task.file_name?.suggested_filename || task.filename;
}
