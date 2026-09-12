import type {
  AiTemplateDraft,
  BackupRead,
  BackupStatus,
  DataRowRead,
  DataRowRevision,
  DataTableDetail,
  DataTableRead,
  DataViewDetail,
  DataViewRead,
  DashboardSummary,
  Extraction,
  Confirmation,
  ExtractionTemplate,
  ExtractionResult,
  LocalModelAction,
  LocalModelList,
  LocalModelStatus,
  ModelStatus,
  ModelProfile,
  ModelProfileDraft,
  ModelProbeResult,
  RestoreResult,
  SystemStatus,
  SystemSettings,
  TableColumnDef,
  Task,
  TaskStatus,
  TaskSummary,
  TemplateDraft,
  TrendPoint,
} from "./types";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export interface LocalExportBinding {
  revision: number;
  enabled: boolean;
  parent_path: string | null;
  destination: string | null;
}

export async function getLocalExportBinding(templateId: string): Promise<LocalExportBinding> {
  return readResponse(await fetch(`${API_BASE_URL}/templates/${encodeURIComponent(templateId)}/local-export`));
}

export async function saveLocalExportBinding(templateId: string, body: { expected_revision: number; enabled: boolean; parent_path: string | null }): Promise<LocalExportBinding> {
  return readResponse(await fetch(`${API_BASE_URL}/templates/${encodeURIComponent(templateId)}/local-export`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }));
}

export function getIntegrationBaseUrl(): string {
  const integrationPath = API_BASE_URL.replace(
    /\/api\/v1\/?$/,
    "/api/integration/v1",
  );
  return new URL(integrationPath, window.location.origin).toString().replace(/\/$/, "");
}


function statusFallback(status: number, action: string): string {
  if (status === 400 || status === 422) return `${action}失败：提交的内容不完整或格式不正确，请检查后重试。`;
  if (status === 401 || status === 403) return `${action}失败：当前没有执行这项操作的权限。`;
  if (status === 404) return `${action}失败：相关内容不存在，可能已被删除。`;
  if (status === 409) return `${action}失败：数据已经发生变化，请刷新后重试。`;
  if (status === 413) return `${action}失败：文件超过当前允许的大小。`;
  if (status >= 500) return `${action}失败：知意服务暂时不可用，请稍后重试。`;
  return `${action}失败，请稍后重试。`;
}

export async function responseErrorMessage(response: Response, action = "请求"): Promise<string> {
  const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  if (typeof body?.detail === "string" && body.detail.trim()) return body.detail.trim();
  return statusFallback(response.status, action);
}

async function readResponse<T>(response: Response): Promise<T> {
  if (response.ok) {
    return response.json() as Promise<T>;
  }

  throw new Error(await responseErrorMessage(response));
}

/** 校验无响应体接口（如 204 删除）是否成功；失败时把后端 detail 抛成友好错误。 */
async function expectOk(response: Response): Promise<void> {
  if (response.ok) return;
  throw new Error(await responseErrorMessage(response));
}

/** fetch wrapper that turns network errors into a friendly Chinese message. */
async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error("无法连接本地后端服务，请确认 API 已启动。");
    }
    throw error;
  }
}

export async function getTasks(options?: {
  limit?: number;
  offset?: number;
  activeOnly?: boolean;
  exportPending?: boolean;
  status?: string;
  search?: string;
  templateId?: string;
  since?: string;
}): Promise<Task[]> {
  const params = new URLSearchParams();
  if (options?.limit != null) params.set("limit", String(options.limit));
  if (options?.offset) params.set("offset", String(options.offset));
  if (options?.activeOnly) params.set("active_only", "true");
  if (options?.exportPending) params.set("export_pending", "true");
  if (options?.status) params.set("status", options.status);
  if (options?.search) params.set("search", options.search);
  if (options?.templateId) params.set("template_id", options.templateId);
  if (options?.since) params.set("since", options.since);
  const query = params.toString();
  return readResponse<Task[]>(
    await apiFetch(`${API_BASE_URL}/tasks${query ? `?${query}` : ""}`),
  );
}

export async function actOnTaskExport(taskId: string, body: { action: "retry" | "skip"; filename?: string; parent_path?: string; acknowledge_uncertain?: boolean }): Promise<Task> {
  return readResponse(await apiFetch(`${API_BASE_URL}/tasks/${encodeURIComponent(taskId)}/export`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }));
}

/** 历史页统计：各状态任务计数，支持与任务列表相同的筛选参数。 */
export async function getTaskSummary(options?: {
  search?: string;
  templateId?: string;
  since?: string;
}): Promise<TaskSummary> {
  const params = new URLSearchParams();
  if (options?.search) params.set("search", options.search);
  if (options?.templateId) params.set("template_id", options.templateId);
  if (options?.since) params.set("since", options.since);
  const query = params.toString();
  return readResponse<TaskSummary>(
    await apiFetch(`${API_BASE_URL}/tasks/summary${query ? `?${query}` : ""}`),
  );
}

export async function getModelStatus(): Promise<ModelStatus> {
  return readResponse<ModelStatus>(await apiFetch(`${API_BASE_URL}/models/status`));
}

export async function getSystemStatus(): Promise<SystemStatus> {
  return readResponse<SystemStatus>(await apiFetch(`${API_BASE_URL}/system/status`));
}

export async function getDashboardSummary(): Promise<DashboardSummary> {
  return readResponse<DashboardSummary>(
    await apiFetch(`${API_BASE_URL}/stats/summary`),
  );
}

/** 仪表盘按天聚合任务数（趋势图与范围统计），不拉全量任务表。 */
export async function getDashboardTrend(days: number): Promise<TrendPoint[]> {
  return readResponse<TrendPoint[]>(
    await apiFetch(`${API_BASE_URL}/stats/trend?days=${days}`),
  );
}

export async function getBackups(): Promise<BackupRead[]> {
  return readResponse<BackupRead[]>(await apiFetch(`${API_BASE_URL}/backups`));
}

export async function getBackupStatus(): Promise<BackupStatus> {
  return readResponse<BackupStatus>(
    await apiFetch(`${API_BASE_URL}/backups/status`),
  );
}

export async function createBackup(): Promise<BackupRead> {
  return readResponse<BackupRead>(
    await apiFetch(`${API_BASE_URL}/backups`, { method: "POST" }),
  );
}

export async function deleteBackup(name: string): Promise<void> {
  await expectOk(await apiFetch(`${API_BASE_URL}/backups/${name}`, { method: "DELETE" }));
}

export async function restoreBackup(name: string): Promise<RestoreResult> {
  return readResponse<RestoreResult>(
    await apiFetch(`${API_BASE_URL}/backups/${name}/restore`, { method: "POST" }),
  );
}

export async function getModelProfiles(): Promise<ModelProfile[]> {
  return readResponse<ModelProfile[]>(
    await apiFetch(`${API_BASE_URL}/models/profiles`),
  );
}

export async function createModelProfile(
  draft: ModelProfileDraft,
): Promise<ModelProfile> {
  return readResponse<ModelProfile>(
    await apiFetch(`${API_BASE_URL}/models/profiles`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    }),
  );
}

export async function updateModelProfile(
  profileId: string,
  expectedVersion: number,
  draft: ModelProfileDraft,
): Promise<ModelProfile> {
  return readResponse<ModelProfile>(
    await apiFetch(`${API_BASE_URL}/models/profiles/${profileId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...draft, expected_version: expectedVersion, clear_api_key: false }),
    }),
  );
}

export async function activateModelProfile(
  profileId: string,
  version: number,
  acknowledgeRemoteDataTransfer = false,
): Promise<ModelProfile> {
  return readResponse<ModelProfile>(
    await apiFetch(`${API_BASE_URL}/models/profiles/${profileId}/activate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        version,
        acknowledge_remote_data_transfer: acknowledgeRemoteDataTransfer,
      }),
    }),
  );
}

export async function probeModelProfile(
  draft: ModelProfileDraft,
  profileId?: string,
): Promise<ModelProbeResult> {
  const query = profileId ? `?profile_id=${encodeURIComponent(profileId)}` : "";
  return readResponse<ModelProbeResult>(
    await apiFetch(`${API_BASE_URL}/models/profiles/probe${query}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    }),
  );
}

export async function archiveModelProfile(
  profileId: string,
): Promise<ModelProfile> {
  return readResponse<ModelProfile>(
    await apiFetch(`${API_BASE_URL}/models/profiles/${profileId}/archive`, {
      method: "POST",
    }),
  );
}

export async function getLocalModelStatus(): Promise<LocalModelStatus> {
  return readResponse<LocalModelStatus>(
    await apiFetch(`${API_BASE_URL}/system/local-model/status`),
  );
}

export async function startLocalModelServer(): Promise<LocalModelAction> {
  return readResponse<LocalModelAction>(
    await apiFetch(`${API_BASE_URL}/system/local-model/start`, {
      method: "POST",
    }),
  );
}

export async function stopLocalModelServer(): Promise<LocalModelAction> {
  return readResponse<LocalModelAction>(
    await apiFetch(`${API_BASE_URL}/system/local-model/stop`, {
      method: "POST",
    }),
  );
}

export async function getLocalModels(): Promise<LocalModelList> {
  return readResponse<LocalModelList>(
    await apiFetch(`${API_BASE_URL}/system/local-model/models`),
  );
}

export async function loadLocalModel(
  modelName: string,
  contextLength: number,
): Promise<LocalModelAction> {
  return readResponse<LocalModelAction>(
    await apiFetch(`${API_BASE_URL}/system/local-model/load`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_name: modelName, context_length: contextLength }),
    }),
  );
}

export async function unloadLocalModel(modelName: string): Promise<LocalModelAction> {
  return readResponse<LocalModelAction>(
    await apiFetch(`${API_BASE_URL}/system/local-model/unload`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_name: modelName }),
    }),
  );
}

export async function uploadTask(
  file: File,
  templateSelection: string,
  targetTableId?: string,
): Promise<Task> {
  const body = new FormData();
  body.append("file", file);
  body.append("template_mode", templateSelection === "smart" ? "smart" : "manual");
  if (templateSelection !== "smart") {
    body.append("template_id", templateSelection);
  }
  if (targetTableId) {
    body.append("target_table_id", targetTableId);
  }

  return readResponse<Task>(
    await apiFetch(`${API_BASE_URL}/tasks`, {
      method: "POST",
      body,
    }),
  );
}

export async function getOnboardingState(): Promise<number> {
  const value = await readResponse<{ completed_version: number }>(
    await apiFetch(`${API_BASE_URL}/user-state/onboarding`),
  );
  return value.completed_version;
}

export async function setOnboardingState(completedVersion: number): Promise<void> {
  await expectOk(await apiFetch(`${API_BASE_URL}/user-state/onboarding`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ completed_version: completedVersion }),
  }));
}

export async function cancelTask(taskId: string): Promise<Task> {
  return readResponse<Task>(
    await apiFetch(`${API_BASE_URL}/tasks/${taskId}/cancel`, {
      method: "POST",
    }),
  );
}

/** 后端 SSE 推送的任务状态变化事件（含文件名与失败原因，供全局日志/toast 使用）。 */
export interface TaskEvent {
  id: string;
  status: TaskStatus;
  filename: string;
  failure_message?: string | null;
  export_status?: string | null;
}

export function subscribeTaskEvents(
  onEvents: (events: TaskEvent[]) => void,
): () => void {
  const source = new EventSource(`${API_BASE_URL}/events`);
  source.addEventListener("task", (raw) => {
    try {
      const payload = JSON.parse((raw as MessageEvent).data) as TaskEvent[];
      if (Array.isArray(payload) && payload.length > 0) {
        onEvents(payload);
      }
    } catch {
      // 忽略坏帧，连接保持
    }
  });
  return () => source.close();
}

export async function retryTask(taskId: string, useCurrentSettings = false): Promise<Task> {
  return readResponse<Task>(
    await apiFetch(`${API_BASE_URL}/tasks/${taskId}/retry${useCurrentSettings ? "?use_current_settings=true" : ""}`, {
      method: "POST",
    }),
  );
}

export async function retryTasksBatch(
  taskIds: string[],
): Promise<{ retried: string[]; skipped: number }> {
  return readResponse<{ retried: string[]; skipped: number }>(
    await apiFetch(`${API_BASE_URL}/tasks/batch-retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_ids: taskIds }),
    }),
  );
}

export async function pauseTask(taskId: string): Promise<Task> {
  return readResponse<Task>(
    await apiFetch(`${API_BASE_URL}/tasks/${taskId}/pause`, {
      method: "POST",
    }),
  );
}

/** 暂停整个队列：中断当前处理中的任务，冻结所有排队任务（Worker 不再启动新任务） */
export async function pauseQueue(): Promise<void> {
  await expectOk(await apiFetch(`${API_BASE_URL}/queue/pause`, { method: "POST" }));
}

/** 恢复整个队列：把所有暂停任务放回队列继续处理 */
export async function resumeQueue(): Promise<void> {
  await expectOk(await apiFetch(`${API_BASE_URL}/queue/resume`, { method: "POST" }));
}

/** 查询队列是否处于暂停状态 */
export async function getQueuePaused(): Promise<boolean> {
  const data = await readResponse<{ paused: boolean }>(
    await apiFetch(`${API_BASE_URL}/queue/status`),
  );
  return data.paused;
}

export async function resumeTask(taskId: string): Promise<Task> {
  return readResponse<Task>(
    await apiFetch(`${API_BASE_URL}/tasks/${taskId}/resume`, {
      method: "POST",
    }),
  );
}

export async function deleteTask(taskId: string): Promise<{ kept_rows: number }> {
  return readResponse<{ kept_rows: number }>(
    await apiFetch(`${API_BASE_URL}/tasks/${taskId}`, { method: "DELETE" }),
  );
}

export async function deleteTasksBatch(
  taskIds: string[],
): Promise<{ deleted: number; kept_rows: number }> {
  return readResponse<{ deleted: number; kept_rows: number }>(
    await apiFetch(`${API_BASE_URL}/tasks/batch-delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_ids: taskIds }),
    }),
  );
}

/* ---- System settings & admin ---- */

export async function getSystemSettings(): Promise<SystemSettings> {
  return readResponse<SystemSettings>(
    await apiFetch(`${API_BASE_URL}/system/settings`),
  );
}

export async function updateSystemSettings(
  settings: Partial<SystemSettings>,
): Promise<SystemSettings> {
  return readResponse<SystemSettings>(
    await apiFetch(`${API_BASE_URL}/system/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    }),
  );
}

export async function clearHistory(confirmText: string): Promise<{ cleared_count: number }> {
  return readResponse<{ cleared_count: number }>(
    await apiFetch(`${API_BASE_URL}/system/admin/clear-history`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm_text: confirmText }),
    }),
  );
}

export interface ClearDataStatus {
  data_directory: string;
  original_directory: string;
  incomplete: boolean;
  result: { state: string; completed_at: string | number; message?: string } | null;
}

export async function getClearDataStatus(): Promise<ClearDataStatus> {
  return readResponse(await apiFetch(`${API_BASE_URL}/system/admin/clear-data`));
}

export async function clearAllLocalData(): Promise<{ scheduled: boolean; state?: string }> {
  return readResponse(await apiFetch(`${API_BASE_URL}/system/admin/clear-data`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm_text: "清除全部本地数据" }),
  }));
}

export async function getExtraction(taskId: string): Promise<Extraction> {
  return readResponse<Extraction>(
    await apiFetch(`${API_BASE_URL}/tasks/${taskId}/result`),
  );
}

export async function updateReview(
  taskId: string,
  expectedVersion: number,
  result: ExtractionResult,
  ignoredIssueIndices: number[] = [],
  filename?: string,
): Promise<Extraction> {
  return readResponse<Extraction>(
    await apiFetch(`${API_BASE_URL}/tasks/${taskId}/review`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_version: expectedVersion,
        result,
        ignored_issue_indices: ignoredIssueIndices,
        filename,
      }),
    }),
  );
}

export async function selectTaskTemplate(
  taskId: string,
  templateId?: string,
  targetTableId?: string,
): Promise<Task> {
  return readResponse<Task>(
    await apiFetch(`${API_BASE_URL}/tasks/${taskId}/template`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template_id: templateId ?? null, target_table_id: targetTableId ?? null }),
    }),
  );
}

export async function acceptTaskInputScope(taskId: string): Promise<Task> {
  return readResponse<Task>(await apiFetch(`${API_BASE_URL}/tasks/${taskId}/input-scope`, { method: "POST" }));
}

export async function confirmTask(
  taskId: string,
  expectedReviewVersion: number,
  targetTableId?: string,
): Promise<Confirmation> {
  return readResponse<Confirmation>(
    await apiFetch(`${API_BASE_URL}/tasks/${taskId}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_review_version: expectedReviewVersion,
        target_table_id: targetTableId ?? null,
      }),
    }),
  );
}

export async function getConfirmation(taskId: string): Promise<Confirmation> {
  return readResponse<Confirmation>(
    await apiFetch(`${API_BASE_URL}/tasks/${taskId}/confirmation`),
  );
}

export function getOriginalFileUrl(taskId: string): string {
  return `${API_BASE_URL}/tasks/${taskId}/file`;
}

export async function getTemplates(includeInactive = false): Promise<ExtractionTemplate[]> {
  const query = includeInactive ? "?include_inactive=true" : "";
  return readResponse<ExtractionTemplate[]>(
    await apiFetch(`${API_BASE_URL}/templates${query}`),
  );
}

export async function updateTemplateSmartPool(
  templateId: string,
  inSmartPool: boolean,
): Promise<ExtractionTemplate> {
  return readResponse<ExtractionTemplate>(
    await apiFetch(`${API_BASE_URL}/templates/${templateId}/smart-pool`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ in_smart_pool: inSmartPool }),
    }),
  );
}

export async function archiveTemplate(templateId: string): Promise<ExtractionTemplate> {
  return readResponse<ExtractionTemplate>(
    await apiFetch(`${API_BASE_URL}/templates/${templateId}/archive`, { method: "POST" }),
  );
}

export async function restoreTemplate(templateId: string): Promise<ExtractionTemplate> {
  return readResponse<ExtractionTemplate>(
    await apiFetch(`${API_BASE_URL}/templates/${templateId}/restore`, { method: "POST" }),
  );
}

export async function deleteTemplate(templateId: string): Promise<void> {
  await expectOk(await apiFetch(`${API_BASE_URL}/templates/${templateId}`, { method: "DELETE" }));
}

/** AI 生成模板草稿：样例文件 + 需求描述 → 字段结构（不落库）。
 *  profileId 可选：指定全局模型方案；缺省用任务激活方案。 */
export async function generateTemplateDraft(
  files: File[],
  requirement: string,
  profileId?: string,
  withExamples = true,
  withRules = false,
): Promise<AiTemplateDraft> {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  form.append("requirement", requirement);
  form.append("with_examples", String(withExamples));
  form.append("with_rules", String(withRules));
  if (profileId) {
    form.append("profile_id", profileId);
  }
  return readResponse<AiTemplateDraft>(
    await apiFetch(`${API_BASE_URL}/templates/generate`, {
      method: "POST",
      body: form,
    }),
  );
}

export async function createTemplate(
  draft: TemplateDraft,
  expectedUpdatedAt?: string,
): Promise<ExtractionTemplate> {
  return readResponse<ExtractionTemplate>(
    await apiFetch(`${API_BASE_URL}/templates`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    }),
  );
}

export async function copyTemplate(
  templateId: string,
): Promise<ExtractionTemplate> {
  return readResponse<ExtractionTemplate>(
    await apiFetch(`${API_BASE_URL}/templates/${templateId}/copy`, {
      method: "POST",
    }),
  );
}

export async function updateTemplate(
  templateId: string,
  expectedVersion: number,
  draft: TemplateDraft,
  expectedUpdatedAt?: string,
): Promise<ExtractionTemplate> {
  return readResponse<ExtractionTemplate>(
    await apiFetch(`${API_BASE_URL}/templates/${templateId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...draft, expected_version: expectedVersion, expected_updated_at: expectedUpdatedAt }),
    }),
  );
}

/* ---- Data tables ---- */
export interface TemplateVersionSummary { version: number; name: string; created_at: string; field_count: number }
export async function getTemplateVersions(id: string, before?: number): Promise<TemplateVersionSummary[]> {
  return readResponse(await apiFetch(`${API_BASE_URL}/templates/${id}/versions${before ? `?before=${before}` : ""}`));
}
export async function getTemplateVersion(id: string, version: number): Promise<ExtractionTemplate> {
  return readResponse(await apiFetch(`${API_BASE_URL}/templates/${id}/versions/${version}`));
}
export async function restoreTemplateVersion(id: string, version: number, expectedVersion: number, expectedUpdatedAt?: string): Promise<ExtractionTemplate> {
  return readResponse(await apiFetch(`${API_BASE_URL}/templates/${id}/versions/${version}/restore`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_version: expectedVersion, expected_updated_at: expectedUpdatedAt }),
  }));
}

export interface TemplateRestoration { id: number; from_version: number; to_version: number; created_at: string }
export async function getTemplateRestorations(id: string): Promise<TemplateRestoration[]> {
  return readResponse(await apiFetch(`${API_BASE_URL}/templates/${id}/restorations`));
}

export async function getTables(): Promise<DataTableRead[]> {
  return readResponse<DataTableRead[]>(await apiFetch(`${API_BASE_URL}/tables`));
}

export async function getTable(
  tableId: string,
  page: number,
  pageSize: number,
  search?: string,
): Promise<DataTableDetail> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  if (search) {
    params.set("search", search);
  }
  return readResponse<DataTableDetail>(
    await apiFetch(`${API_BASE_URL}/tables/${tableId}?${params.toString()}`),
  );
}

export async function createTable(
  name: string,
  templateKey: string,
): Promise<DataTableRead> {
  return readResponse<DataTableRead>(
    await apiFetch(`${API_BASE_URL}/tables`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, template_key: templateKey }),
    }),
  );
}

export async function renameTable(
  tableId: string,
  name: string,
): Promise<DataTableRead> {
  return readResponse<DataTableRead>(
    await apiFetch(`${API_BASE_URL}/tables/${tableId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  );
}

export async function deleteTable(tableId: string): Promise<void> {
  await expectOk(await apiFetch(`${API_BASE_URL}/tables/${tableId}`, { method: "DELETE" }));
}

export async function addCustomColumn(
  tableId: string,
  label: string,
  section: "header" | "item",
): Promise<TableColumnDef> {
  return readResponse<TableColumnDef>(
    await apiFetch(`${API_BASE_URL}/tables/${tableId}/columns`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label, section }),
    }),
  );
}

export async function deleteCustomColumn(tableId: string, columnKey: string): Promise<void> {
  await expectOk(await apiFetch(
    `${API_BASE_URL}/tables/${tableId}/columns/${encodeURIComponent(columnKey)}`,
    { method: "DELETE" },
  ));
}

export async function addDataRow(
  tableId: string,
  values: Record<string, unknown>,
): Promise<DataRowRead> {
  return readResponse<DataRowRead>(
    await apiFetch(`${API_BASE_URL}/tables/${tableId}/rows`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values, editor: "local-user" }),
    }),
  );
}

export async function deleteDataRows(
  tableId: string,
  rowIds: number[],
): Promise<void> {
  await expectOk(
    await apiFetch(`${API_BASE_URL}/tables/${tableId}/rows`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ row_ids: rowIds }),
    }),
  );
}

export async function mergeTables(
  tableIds: string[],
  name: string,
): Promise<DataTableRead> {
  return readResponse<DataTableRead>(
    await apiFetch(`${API_BASE_URL}/tables/merge`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ table_ids: tableIds, name }),
    }),
  );
}

export function getTableExportUrl(tableId: string, format: "xlsx" | "csv" | "json" = "xlsx"): string {
  return `${API_BASE_URL}/tables/${tableId}/export.${format}`;
}

export function getTableViewsExportUrl(tableId: string): string {
  return `${API_BASE_URL}/tables/${tableId}/export-views.xlsx`;
}

export async function updateDataRow(
  tableId: string,
  rowId: number,
  expectedVersion: number,
  changes: Record<string, unknown>,
): Promise<DataRowRead> {
  return readResponse<DataRowRead>(
    await apiFetch(`${API_BASE_URL}/tables/${tableId}/rows/${rowId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_version: expectedVersion,
        changes,
        editor: "local-user",
      }),
    }),
  );
}

export async function getTableViews(
  tableId: string,
): Promise<DataViewRead[]> {
  return readResponse<DataViewRead[]>(
    await apiFetch(`${API_BASE_URL}/tables/${tableId}/views`),
  );
}

export async function getRowRevisions(
  tableId: string,
  rowId: number,
): Promise<DataRowRevision[]> {
  return readResponse<DataRowRevision[]>(
    await apiFetch(`${API_BASE_URL}/tables/${tableId}/rows/${rowId}/revisions`),
  );
}

export async function createSplitViews(
  tableId: string,
  fieldKey: string,
  groupBy: "field" | "file" = "field",
): Promise<DataViewRead[]> {
  return readResponse<DataViewRead[]>(
    await apiFetch(`${API_BASE_URL}/tables/${tableId}/split`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ field_key: fieldKey, group_by: groupBy }),
    }),
  );
}

export async function getTableView(
  tableId: string,
  viewId: string,
  page: number,
  pageSize: number,
  search?: string,
): Promise<DataViewDetail> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  if (search) params.set("search", search);
  return readResponse<DataViewDetail>(
    await fetch(
      `${API_BASE_URL}/tables/${tableId}/views/${viewId}?${params.toString()}`,
    ),
  );
}

/* ---- Integration status ---- */

export async function getIntegrationStatus(): Promise<{
  enabled: boolean;
  message: string;
}> {
  const base = getIntegrationBaseUrl();
  try {
    const response = await fetch(`${base}/capabilities`);
    if (response.ok) {
      return { enabled: true, message: "集成接口已启用，本机可直接访问。" };
    }
    if (response.status === 503) {
      return {
        enabled: false,
        message: "未配置读写密钥，集成接口未启用。",
      };
    }
    if (response.status === 401) {
      return { enabled: true, message: "集成接口已启用，需密钥访问。" };
    }
    return {
      enabled: false,
      message: "暂时无法确认集成接口状态，请稍后刷新。",
    };
  } catch {
    return { enabled: false, message: "无法连接集成接口。" };
  }
}

/* ---- Integration config management (密钥生成/撤销 + MCP 权限开关) ---- */

export interface IntegrationSettings {
  read_token_set: boolean;
  write_token_set: boolean;
  task_read: boolean;
  template_read: boolean;
  result_read: boolean;
  data_read: boolean;
  task_control: boolean;
  write_enabled: boolean;
  file_access: boolean;
  file_roots: string[];
}

export interface McpRuntimeConfig {
  server_name: string;
  server: { command: string; args: string[]; env?: Record<string, string> };
  mcp_servers: { mcpServers: Record<string, { command: string; args: string[]; env?: Record<string, string> }> };
  runtime: "installed" | "development";
}

export async function getIntegrationSettings(): Promise<IntegrationSettings> {
  return readResponse<IntegrationSettings>(
    await apiFetch(`${API_BASE_URL}/integration/settings`),
  );
}

export async function getMcpRuntimeConfig(): Promise<McpRuntimeConfig> {
  return readResponse<McpRuntimeConfig>(
    await apiFetch(`${API_BASE_URL}/integration/settings/mcp-config`),
  );
}

/** 生成新密钥并落盘；返回的明文只出现这一次。 */
export async function generateIntegrationKey(
  kind: "read" | "write",
): Promise<{ kind: string; token: string }> {
  return readResponse(
    await apiFetch(`${API_BASE_URL}/integration/settings/keys`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind }),
    }),
  );
}

export async function revokeIntegrationKey(
  kind: "read" | "write",
): Promise<{ revoked: boolean }> {
  return readResponse(
    await apiFetch(`${API_BASE_URL}/integration/settings/keys/revoke`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind }),
    }),
  );
}

export async function saveIntegrationPermissions(payload: {
  task_read: boolean;
  template_read: boolean;
  result_read: boolean;
  data_read: boolean;
  task_control: boolean;
  write_enabled: boolean;
  file_access: boolean;
  file_roots: string[];
}): Promise<{ saved: boolean }> {
  return readResponse(
    await apiFetch(`${API_BASE_URL}/integration/settings/permissions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function getTaskDiagnostics(taskId: string): Promise<{ detail: string; code: string | null; attempt: number }> {
  return readResponse(await apiFetch(`${API_BASE_URL}/tasks/${taskId}/diagnostics`));
}

export async function previewTemplatePattern(pattern: string, text: string): Promise<{ valid: boolean; matches: boolean; message: string }> {
  return readResponse(await apiFetch(`${API_BASE_URL}/templates/rules/preview-pattern`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pattern, text }),
  }));
}
