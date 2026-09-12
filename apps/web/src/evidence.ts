import type { Extraction } from "./types";
import { rangeLabel } from "./components/InputScopeDetails";

type FieldEvidence = NonNullable<Extraction["evidence"]>[number];

export function evidenceDescription(evidence: FieldEvidence | undefined): string {
  if (!evidence) return "该字段没有可用的来源定位";
  if (evidence.status === "user_edited") return "该值经过人工修改，原定位已失效";
  const location = evidence.location ? rangeLabel(evidence.location) : null;
  if (evidence.status === "located" && evidence.location_verified) {
    return `已定位${location ? ` · ${location}` : "到原文件区域"}${evidence.quote ? `：${evidence.quote}` : ""}`;
  }
  if (evidence.status === "page_only" && evidence.location_verified && evidence.page_number) {
    return `仅确认来自${location || `第 ${evidence.page_number} 页`}，暂无可靠区域`;
  }
  if (location) return `本次输入包含 ${location}；尚未确认该字段的具体位置`;
  if (evidence.source === "model_reported" && evidence.quote) {
    return `模型提供了来源摘录，位置尚未核验：${evidence.quote}`;
  }
  return "尚无可靠的字段位置，请结合输入范围和完整原件核对";
}
