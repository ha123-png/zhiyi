import { useEffect, useRef, useState } from "react";
import { FileUp, History, LoaderCircle, UploadCloud } from "lucide-react";
import { getTemplates } from "../api";
import { Icon } from "./Icon";
import type { ExtractionTemplate } from "../types";

interface UploadZoneProps {
  busy: boolean;
  message?: string | null;
  onUpload: (files: File[], templateSelection: string) => Promise<void>;
  onNavigateHistory?: () => void;
}

export function UploadZone({ busy, message, onUpload, onNavigateHistory }: UploadZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [matchMode, setMatchMode] = useState<"auto" | "manual">("auto");
  const [manualTemplate, setManualTemplate] = useState("");
  const [templates, setTemplates] = useState<ExtractionTemplate[]>([]);
  const [dragOver, setDragOver] = useState(false);

  useEffect(() => {
    void getTemplates().then(setTemplates).catch(() => setTemplates([]));
  }, []);

  const templateSelection = matchMode === "auto" ? "smart" : manualTemplate;

  async function submitFiles(files: FileList | null) {
    if (!files?.length || busy) {
      return;
    }
    if (matchMode === "manual" && !manualTemplate && templates.length > 0) {
      setManualTemplate(templates[0].id);
      await onUpload(Array.from(files), templates[0].id);
      return;
    }
    await onUpload(Array.from(files), templateSelection);
  }

  return (
    <section className="upload-panel" aria-labelledby="upload-title">
      <div className="extract-topbar">
        <div className="extract-topbar-left">
          <label className="radio-label">
            <input
              type="radio"
              checked={matchMode === "auto"}
              onChange={() => setMatchMode("auto")}
              name="matchMode"
            />
            智能匹配
          </label>
          <label className="radio-label">
            <input
              type="radio"
              checked={matchMode === "manual"}
              onChange={() => setMatchMode("manual")}
              name="matchMode"
            />
            手动选择
          </label>
          {matchMode === "manual" ? (
            <select
              aria-label="选择模板"
              className="form-select extract-topbar-select"
              value={manualTemplate}
              onChange={(event) => setManualTemplate(event.target.value)}
            >
              {templates.length === 0 ? (
                <option value="">暂无模板</option>
              ) : (
                templates.map((template) => (
                  <option key={template.id} value={template.id}>
                    {template.name}{template.is_system ? "（内置）" : ""}
                  </option>
                ))
              )}
            </select>
          ) : null}
        </div>
        {onNavigateHistory ? (
          <button
            className="btn ghost"
            onClick={onNavigateHistory}
            type="button"
          >
            <Icon icon={History} size={16} />
            文件历史
          </button>
        ) : null}
      </div>

      <input
        ref={inputRef}
        accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
        className="visually-hidden"
        disabled={busy}
        multiple
        onChange={(event) => {
          void submitFiles(event.target.files);
          event.target.value = "";
        }}
        type="file"
      />
      <button
        className={`drop-zone${dragOver ? " drag-over" : ""}`}
        disabled={busy}
        onClick={() => inputRef.current?.click()}
        onDragLeave={(event) => {
          event.preventDefault();
          setDragOver(false);
        }}
        onDragOver={(event) => {
          event.preventDefault();
          if (!dragOver) {
            setDragOver(true);
          }
        }}
        onDrop={(event) => {
          event.preventDefault();
          setDragOver(false);
          void submitFiles(event.dataTransfer.files);
        }}
        type="button"
      >
        <span className="drop-zone-icon">
          <Icon icon={busy ? LoaderCircle : UploadCloud} size={32} />
        </span>
        <strong className="drop-zone-title">
          {busy ? "正在保存文件..." : "拖拽文件到此处"}
        </strong>
        <span className="drop-zone-desc">
          支持 PDF、JPG 和 PNG。单个文件最大 50 MB · 批量任务将按顺序处理。
        </span>
        <span className="drop-zone-hint">
          <Icon icon={FileUp} size={15} />
          点击选择文件
        </span>
      </button>

      <div className="smart-note">
        <span>{message ?? "文件会先安全保存，再进入本地处理队列。"}</span>
      </div>
    </section>
  );
}
