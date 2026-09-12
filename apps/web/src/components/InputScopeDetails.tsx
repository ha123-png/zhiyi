import { Disclosure } from "./Disclosure";
import { useState } from "react";
import { acceptTaskInputScope } from "../api";
import type { InputScope, SourceRange, Task } from "../types";

function columnName(count: number) {
  let name = "";
  while (count > 0) { count -= 1; name = String.fromCharCode(65 + count % 26) + name; count = Math.floor(count / 26); }
  return name;
}

export function rangeLabel(range: SourceRange): string {
  if (range.kind === "sheet_row") return `${range.container}!A${range.start}:${columnName(range.columns || 1)}${range.end}`;
  const names: Record<string, string> = { page: "页", frame: "帧", line: "行", paragraph: "段落", table_row: "行", image: "图片", document_part: "部分" };
  return `${range.container ? `${range.container} · ` : ""}第 ${range.start}${range.end === range.start ? "" : `–${range.end}`} ${names[range.kind] || "单元"}${range.character_start ? ` · 字符 ${range.character_start}–${range.character_end}` : ""}`;
}

export function InputScopeDetails({ scope, label = "本次提取输入范围", originalAvailable = true }: { scope?: InputScope | null; label?: string; originalAvailable?: boolean }) {
  if (!scope) return null;
  return <Disclosure className="input-scope-details" title={<>{label} · {scope.coverage === "partial" ? "局部读取" : "完整提供"}</>}>
    <div className="scope-content">
      <p>{originalAvailable ? "完整原件仍保存在知意。" : "此处保留当时的读取范围；当前内容没有可打开的原件关联。"}输入覆盖范围不代表提取正确率或任务完成比例。</p>
      <p><strong>提供范围：</strong>{scope.selected.map(rangeLabel).join("；")}</p>
      {scope.omitted.length > 0 && <>
        <p><strong>未提供范围：</strong></p>
        <ul>{scope.omitted.map((part, index) => <li key={index}>{rangeLabel(part.location)} · {{ input_budget: "达到设置的读取上限", images_disabled: "未开启图片读取", unsupported_image: "暂不支持的图片", unsupported_structure: "暂不支持的文档部分" }[part.reason]}</li>)}</ul>
        <p>未提供的内容可能影响判断，不能把结果当作全文摘要或全部记录。</p>
      </>}
      <p>规则：{scope.rule === "all" ? "按原始顺序提供全部已列内容" : scope.rule === "prefix_v1" ? "从文件开头连续读取，到设置上限停止" : "按旧任务当时的设置选择开头与结尾，范围保留用于追溯"}。</p>
      {scope.notes.map((note, index) => <p key={index}>{note}</p>)}
    </div>
  </Disclosure>;
}

export function PartialInputAction({ task, onUpdated }: { task: Task; onUpdated?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState("");
  return <div className="callout">
    <strong>需要确认局部读取</strong>
    <p>文件已保存，尚未进行正式提取。当前无法提供完整输入；你可以仅提取所示范围，也可以取消任务后缩小文件内容。</p>
    <InputScopeDetails scope={task.planned_scope} label="计划输入范围" />
    <button type="button" className="btn primary sm" disabled={busy || submitted} onClick={async () => {
      setBusy(true); setError("");
      try { await acceptTaskInputScope(task.id); setSubmitted(true); onUpdated?.(); }
      catch (reason) { setError(reason instanceof Error ? reason.message : "确认失败，请重试。"); }
      finally { setBusy(false); }
    }}>{submitted ? "已确认，等待处理" : busy ? "正在确认…" : "仅提取所示范围"}</button>
    {error && <p role="alert">{error}</p>}
  </div>;
}
