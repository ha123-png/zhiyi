import type { FileNameState } from "../types";
import { Disclosure } from "./Disclosure";

export function FileNameConfirmation({ original, state, value, onChange, disabled }: {
  original: string; state?: FileNameState | null; value?: string; onChange: (value: string) => void; disabled?: boolean;
}) {
  if (!state) return null;
  const chosen = value ?? state.confirmed_filename ?? state.suggested_filename ?? original;
  return <Disclosure className="presentation-settings" title={`文件名称 · ${chosen === original ? "保留原名" : "内容名称"}`}>
    <p className="support" style={{ overflowWrap: "anywhere" }}>上传原名：{original}</p>
    <label className="form-label" htmlFor="confirmed-file-name">文件名称（保留扩展名）</label>
    <input id="confirmed-file-name" className="form-input" disabled={disabled} value={chosen} onChange={(event) => onChange(event.target.value)} />
    <p className="support">名称自动随提取结果生效；手动修改随保存结果生效。上传原名和原件保留，已有外部副本不变。</p>
  </Disclosure>;
}
