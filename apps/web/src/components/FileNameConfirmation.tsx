import type { FileNameState } from "../types";

export function FileNameConfirmation({ original, state, value, onChange, disabled }: {
  original: string; state?: FileNameState | null; value?: string; onChange: (value: string) => void; disabled?: boolean;
}) {
  if (!state) return null;
  const chosen = value ?? state.confirmed_filename ?? original;
  return <details className="presentation-settings" open={state.status === "pending" || undefined}>
    <summary>文件名称 · 随结果一起确认</summary>
    <p className="support" style={{ overflowWrap: "anywhere" }}>上传原名：{original}</p>
    <p className="support" style={{ overflowWrap: "anywhere" }}>建议：{state.suggested_filename}</p>
    <p className="support">{state.explanation}</p>
    <label className="form-label" htmlFor="confirmed-file-name">确认后的名称（保留扩展名）</label>
    <input id="confirmed-file-name" className="form-input" disabled={disabled} value={chosen} onChange={(event) => onChange(event.target.value)} />
    <div className="button-row">
      <button type="button" className="btn ghost sm" disabled={disabled} onClick={() => onChange(state.suggested_filename)}>采用建议</button>
      <button type="button" className="btn ghost sm" disabled={disabled} onClick={() => onChange(original)}>保留原名</button>
    </div>
    <p className="support">点击保存结果时确认此名称。原名和内部原件保持可追溯；已有外部副本不会被改名。</p>
  </details>;
}
