import { useEffect, useRef, useState } from "react";

export function ImportSheetDialog({ filename, sheets, busy, error, onChoose, onClose }: {
  filename: string; sheets: string[]; busy: boolean; error: string | null;
  onChoose: (sheet: string) => void; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [selected, setSelected] = useState("");
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => previous?.focus();
  }, []);
  return <dialog ref={dialog} className="modal-card" style={{maxWidth: 480}} aria-labelledby="import-sheet-title"
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className="modal-head"><h3 id="import-sheet-title">选择要导入的工作表</h3></div>
    <div className="modal-body">
      <p className="support" style={{overflowWrap: "anywhere"}}>{filename}</p>
      <label className="form-field"><span>工作表</span>
        <select autoFocus className="form-input" value={selected} disabled={busy} onChange={event => setSelected(event.target.value)}>
          <option value="" disabled>请选择一页</option>
          {sheets.map(sheet => <option key={sheet} value={sheet}>{sheet}</option>)}
        </select>
      </label>
      <p className="support">本次只导入所选工作表，其他工作表保持原样。</p>
      {error && <p className="callout danger" role="alert">{error}</p>}
    </div>
    <div className="modal-foot"><button className="btn secondary" disabled={busy} onClick={onClose}>取消</button>
      <button className="btn primary" disabled={busy || !selected} onClick={() => onChoose(selected)}>{busy ? "正在导入…" : "导入此工作表"}</button></div>
  </dialog>;
}
