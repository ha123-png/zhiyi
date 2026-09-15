export interface BusinessContext {
  table_id?: string | null;
  table_name?: string | null;
  row_ids?: number[] | null;
  search?: string;
  task_id?: string | null;
  template_id?: string | null;
  template_version?: number | null;
  view_id?: string | null;
  workspace?: boolean;
  table_ids?: string[];
  task_ids?: string[];
  template_ids?: string[];
  mode?: "read" | "assist" | "delegate";
  capabilities?: ("data" | "templates" | "tasks" | "organize")[];
}
export interface Reference {
  kind: string;
  id: string;
  label?: string;
  row_id?: number;
  version?: number;
}
export type ChartType =
  | "bar"
  | "horizontal_bar"
  | "line"
  | "area"
  | "stacked_bar"
  | "composed"
  | "pie"
  | "donut"
  | "scatter"
  | "metric"
  | "table";
export interface ChartSpec {
  analysis_id: string;
  type: ChartType;
  title: string;
  x: string;
  series: string[];
  series_labels?: Record<string, string>;
}
export interface Analysis {
  analysis_id: string;
  source: {
    table_id: string;
    table_name: string;
    row_count: number;
    document_count: number;
    grain: string;
    generated_at: string;
    scope_description?: string | null;
    request: Record<string, unknown>;
  };
  data?: Record<string, string | number | null>[];
  rows?: {
    row_id: number;
    version: number;
    task_id: string | null;
    values: Record<string, unknown>;
  }[];
  columns?: { key: string; label: string }[];
  metric_keys?: string[];
  metric_labels?: Record<string, string>;
  totals?: Record<string, number | null>;
  group_count?: number;
  truncated: boolean;
  warnings: string[];
}
export interface ToolResult extends Partial<Analysis> {
  error?: string;
  note?: string;
  reference?: Reference;
  original?: Reference;
  chart?: ChartSpec;
  analysis?: Analysis;
  draft?: Record<string, unknown>;
  affected?: {
    row_id: number;
    version: number;
    before: Record<string, unknown>;
    after: Record<string, unknown>;
  }[];
  [key: string]: unknown;
}
export interface ToolRecord {
  id: string;
  name: string;
  status: string;
  result: ToolResult;
}
export type Part =
  | { type: "text"; text: string }
  | ({ type: "tool" } & ToolRecord);
export interface Message {
  id: string;
  position?: number;
  role: "user" | "assistant";
  parts: Part[];
  context: BusinessContext;
  created_at: string;
}
export interface Run {
  execution_kind?: "model" | "analysis_refresh";
  id: string;
  message_id?: string;
  stream_cursor?: number;
  status: string;
  error: string | null;
  model: string;
  profile_id: string;
  profile_version: number;
}
export interface Thread {
  id: string;
  title: string;
  archived: boolean;
  profile_id: string | null;
  updated_at: string;
}
export interface ThreadDetail extends Thread {
  last_model_run?: Run | null;
  partial?: boolean;
  has_more?: boolean;
  oldest_position?: number;
  messages: Message[];
  runs: Run[];
  tools: ToolRecord[];
}
