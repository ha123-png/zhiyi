import { useRef, useState } from "react";
import { previewTemplatePattern } from "../api";

export function PatternRulePreview({ pattern, disabled }: { pattern: string; disabled: boolean }) {
  const [sample, setSample] = useState("");
  const [result, setResult] = useState<{ pattern: string; sample: string; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const request = useRef(0);
  return <div className="rule-pattern-preview">
    <p className="support">按整个字段匹配，最多 512 字；不是在文本中搜索。例：<code>{"PO-\\d{3}"}</code> 可匹配 PO-001，不能匹配 PO-01 或“编号 PO-001”。</p>
    <p className="support">支持普通文字、[A-Z]、[0-9]、\d、\s 和重复次数 *、+、?、{'{3}'}、{'{2,5}'}；不支持分组 ()、分支 | 或回溯引用。表达式最多 128 字。</p>
    <label><span>试填一个值</span><input className="form-input" disabled={disabled} maxLength={512} value={sample} placeholder="例如：PO-001" onChange={event => setSample(event.target.value)} /></label>
    <button type="button" className="btn ghost sm" disabled={disabled || busy || !pattern} onClick={async () => {
      const current = ++request.current; setBusy(true);
      try {
        const response = await previewTemplatePattern(pattern, sample);
        if (current === request.current) setResult({ pattern, sample, message: response.message });
      } catch (error) { if (current === request.current) setResult({ pattern, sample, message: error instanceof Error ? error.message : "检查失败，请重试。" }); }
      finally { if (current === request.current) setBusy(false); }
    }}>{busy ? "检查中…" : "检查格式"}</button>
    {result && result.pattern === pattern && result.sample === sample ? <p className="support" role="status">{result.message}</p> : null}
  </div>;
}
