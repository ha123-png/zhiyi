export type NavigationKey =
  | "workspace"
  | "extract"
  | "tables"
  | "history"
  | "templates"
  | "dashboard"
  | "backups"
  | "connections"
  | "guide"
  | "settings";

export type TaskStatus =
  | "created"
  | "waiting_for_template"
  | "queued"
  | "processing"
  | "validating"
  | "needs_review"
  | "completed"
  | "paused"
  | "cancelled"
  | "failed";

export type TemplateMode = "smart" | "manual" | "invoice" | "delivery";

export interface Task {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  page_count?: number;
  sha256: string;
  template_mode: TemplateMode;
  template_id: string | null;
  template_version: number | null;
  candidate_templates: Array<{
    id: string;
    version: number;
    name: string;
    description: string;
  }>;
  status: TaskStatus;
  failure_message?: string | null;
  duplicate_of_task_id: string | null;
  record_count?: number;
  processing_elapsed_seconds?: number | null;
  processing_engine?: "multimodal_model" | null;
  processing_model?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at: string;
}

/** 历史页统计：各状态任务计数（SQL 聚合，不加载任务全表）。 */
export interface TaskSummary {
  total: number;
  completed: number;
  needs_review: number;
  failed: number;
}

/** 仪表盘按天聚合：趋势图与范围统计（SQL 聚合，不加载任务全表）。 */
export interface TrendPoint {
  date: string;
  total: number;
  completed: number;
  failed: number;
}

/** 业务备份条目（普通用户备份/恢复页）。 */
export interface BackupRead {
  name: string;
  size_bytes: number;
  created_at: string;
}

export interface BackupStatus {
  backup_dir: string;
  count: number;
  retention: number;
  free_bytes: number;
  last_success_at: string | null;
  database_size_bytes: number;
  uploads_size_bytes: number;
  restore_state: "succeeded" | "failed" | null;
  restore_message: string | null;
}

export interface RestoreResult {
  rollback_dir: string | null;
  restart_required: boolean;
  scheduled: boolean;
}

export interface ModelStatus {
  connected: boolean;
  provider: string;
  configured_model: string;
  available_models: string[];
  configuration_error?: string | null;
}

export type ModelProviderName = "lm_studio" | "ollama" | "openai_compatible";

export interface ModelProfile {
  id: string;
  version: number;
  current_version: number;
  name: string;
  provider: ModelProviderName;
  base_url: string;
  model_name: string;
  reasoning_effort: string | null;
  timeout_seconds: number;
  context_length: number;
  temperature: number | null;
  multimodal: boolean | null;
  has_api_key: boolean;
  is_remote: boolean;
  remote_data_acknowledged: boolean;
  is_active: boolean;
  active_version: number | null;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface ModelProfileDraft {
  name: string;
  provider: ModelProviderName;
  base_url: string;
  model_name: string;
  reasoning_effort: string | null;
  timeout_seconds: number;
  context_length: number;
  temperature: number | null;
  multimodal: boolean | null;
  api_key?: string;
  acknowledge_remote_data_transfer: boolean;
}

export interface ModelProbeResult {
  connected: boolean;
  model_listed: boolean;
  multimodal: boolean | null;
  message: string;
  available_models: string[];
}

export interface LocalModelStatus {
  provider: ModelProviderName;
  base_url: string;
  running: boolean;
  message: string;
  loaded: string[];
  installed?: boolean;
  install_url?: string | null;
}

export interface DashboardSummary {
  average_elapsed_seconds: number | null;
  processed_count: number;
  failed_count: number;
  success_rate: number | null;
}

export interface LocalModelAction {
  ok: boolean;
  message: string;
  detail?: string | null;
}

export interface LocalModelList {
  provider: string;
  available: string[];
  loaded: string[];
}

export interface ComponentStatus {
  connected: boolean;
  message: string;
}

export interface SystemStatus {
  api: ComponentStatus;
  worker: ComponentStatus;
  model: ComponentStatus;
  model_provider: string;
  configured_model: string;
}

export interface LineItem {
  name: string | null;
  specification: string | null;
  unit: string | null;
  quantity: number | null;
  unit_price: number | null;
  amount: number | null;
  tax_rate: string | null;
  tax_amount: number | null;
  remarks?: string | null;
}

export interface DocumentResult {
  document_type: string | null;
  seller_name: string | null;
  buyer_name: string | null;
  seller_tax_id?: string | null;
  buyer_tax_id?: string | null;
  seller_contact?: string | null;
  buyer_contact?: string | null;
  buyer_address?: string | null;
  document_number: string | null;
  document_date: string | null;
  amount_before_tax: number | null;
  tax_amount: number | null;
  total_amount: number | null;
  total_quantity?: number | null;
  remarks?: string | null;
  items: LineItem[];
}

export type TemplateValue = string | number | boolean | null;

export interface TemplateResult {
  header: Record<string, TemplateValue>;
  items: Array<Record<string, TemplateValue>>;
}

export type ExtractionResult = DocumentResult | TemplateResult;

export interface Extraction {
  task_id: string;
  document_kind: "invoice" | "delivery" | "custom";
  template_id: string | null;
  template_version: number | null;
  template: ExtractionTemplate | null;
  model_name: string;
  prompt_version: string;
  elapsed_seconds: number;
  review_version: number;
  original_result: ExtractionResult;
  result: ExtractionResult;
  validation_issues: Array<{
    code: string;
    field: string;
    message: string;
    severity: "warning" | "error";
    ignored?: boolean;
  }>;
  evidence?: Array<{
    field_path: string;
    page_number: number | null;
    region: {
      x: number;
      y: number;
      width: number;
      height: number;
    } | null;
    quote: string | null;
    status: "page_only" | "located" | "unavailable" | "user_edited";
    source: "system" | "model_reported" | "user";
    location_verified: boolean;
  }>;
}

export interface Confirmation {
  task_id: string;
  table_id: string;
  table_name: string;
  review_version: number;
  row_count: number;
  confirmed_at: string;
}

export interface TemplateField {
  key?: string;
  label: string;
  section: "header" | "item";
  example: string;
  instructions: string;
  value_type: "text" | "number" | "date" | "boolean";
}

/** AI 生成模板草稿字段（无内部 key，保存时后端生成） */
export interface AiDraftField {
  label: string;
  section: "header" | "item";
  example: string;
  value_type: "text" | "number" | "date" | "boolean";
}

/** AI 生成模板草稿：只返回结构，不落库，人工确认后保存 */
export interface AiTemplateDraft {
  name: string;
  description: string;
  fields: AiDraftField[];
}

export type RuleExpression =
  | { op: "field"; path: string }
  | { op: "sum"; path: string }
  | { op: "constant"; value: number }
  | {
      op: "add" | "subtract" | "multiply" | "divide";
      left: RuleExpression;
      right: RuleExpression;
    };

export type DeterministicRule =
  | { kind: "required"; field: string; severity?: "warning" | "error" }
  | {
      kind: "range";
      field: string;
      minimum?: number | null;
      maximum?: number | null;
      severity?: "warning" | "error";
    }
  | {
      kind: "enum";
      field: string;
      values: Array<string | number | boolean>;
      severity?: "warning" | "error";
    }
  | { kind: "pattern"; field: string; pattern: string; severity?: "warning" | "error" }
  | {
      kind: "equation";
      field: string;
      left: RuleExpression;
      right: RuleExpression;
      tolerance?: number;
      severity?: "warning" | "error";
    };

export interface ExtractionTemplate {
  id: string;
  version: number;
  is_system: boolean;
  is_active?: boolean;
  in_smart_pool?: boolean;
  builtin_key: string | null;
  source_template_id: string | null;
  name: string;
  description: string;
  extra_instructions: string;
  fields: TemplateField[];
  validation_rules: string[];
  deterministic_rules: DeterministicRule[];
  output_mapping: Record<string, string>;
  created_at: string;
  updated_at: string;
}

export type TemplateDraft = Pick<
  ExtractionTemplate,
  | "name"
  | "description"
  | "extra_instructions"
  | "fields"
  | "validation_rules"
  | "deterministic_rules"
  | "output_mapping"
>;

/* ---- Data tables / rows / views（对应后端 schemas/data_tables.py） ---- */

export interface DataRowRevision {
  id: number;
  row_id: number;
  version: number;
  operation: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  editor: string;
  created_at: string;
}

export interface DataTableRead {
  id: string;
  name: string;
  template_key: string;
  template_version: string;
  document_kind: string;
  row_count: number;
  created_at: string;
}

export interface DataRowRead {
  id: number;
  task_id: string | null;
  item_index: number;
  version: number;
  values: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface TableColumnDef {
  key: string;
  label: string;
  value_type: string;
  section?: string | null;
  user_defined?: boolean;
}

export interface DataTableDetail extends DataTableRead {
  columns: TableColumnDef[];
  page: number;
  page_size: number;
  rows: DataRowRead[];
}

export interface DataRowUpdate {
  expected_version: number;
  changes: Record<string, unknown>;
  editor?: string;
}

export interface DataViewRead {
  id: string;
  table_id: string;
  name: string;
  field_key: string;
  field_value: unknown;
  row_count: number;
  created_at: string;
  updated_at: string;
}

export interface DataViewDetail extends DataViewRead {
  source_table_name: string;
  columns: TableColumnDef[];
  page: number;
  page_size: number;
  rows: DataRowRead[];
}

export interface SystemSettings {
  image_convert: boolean;
  office_convert: boolean;
  /** Word 是否连同内嵌图片一起识别：true=文字+图片，false=只提取文本 */
  word_include_images: boolean;
}
