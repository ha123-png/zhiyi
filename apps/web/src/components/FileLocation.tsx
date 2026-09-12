import { useState } from "react";
export function FileLocation({ path }: { path: string }) {
  const [notice, setNotice] = useState("");
  return <div><div className="source-location"><p className="source-value">{path}</p>
    <button className="btn ghost sm" type="button" aria-label="复制文件位置" onClick={async () => {
      try { await navigator.clipboard.writeText(path); setNotice("已复制"); }
      catch { setNotice("复制未成功，可选中路径后复制。"); }
    }}>复制</button></div>{notice && <span className="small muted" role="status">{notice}</span>}</div>;
}
