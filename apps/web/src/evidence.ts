import type { Extraction } from "./types";
import { rangeLabel } from "./components/InputScopeDetails";

type FieldEvidence = NonNullable<Extraction["evidence"]>[number];

export function evidenceDescription(evidence: FieldEvidence | undefined): string | null {
  if (!evidence?.location_verified) return null;
  const location = evidence.location ? rangeLabel(evidence.location) : null;
  if (evidence.status === "located") {
    return `已定位${location ? ` · ${location}` : "到原文件区域"}${evidence.quote ? `：${evidence.quote}` : ""}`;
  }
  if (evidence.status === "page_only" && evidence.page_number) {
    return `仅确认来自${location || `第 ${evidence.page_number} 页`}，暂无可靠区域`;
  }
  return null;
}
