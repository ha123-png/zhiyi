import { useEffect, useRef } from "react";

interface EditableTextProps {
  editing: boolean;
  value: string;
  onCommit: (raw: string) => void;
  onCancel?: () => void;
  className?: string;
}

/**
 * 可编辑文本：单击后进入 contentEditable，内容始终保留，
 * 进入编辑时自动聚焦并把光标放到文字末尾，避免“点了变空白”。
 * Enter 提交、Esc 取消、失焦提交。
 */
export function EditableText({
  editing,
  value,
  onCommit,
  onCancel,
  className,
}: EditableTextProps) {
  const ref = useRef<HTMLElement | null>(null);
  // Esc 取消时抑制随后 blur 触发的 commit，避免"Esc 反而保存"
  const cancelledRef = useRef(false);

  useEffect(() => {
    if (!editing || !ref.current) return;
    const el = ref.current;
    el.focus();
    const range = document.createRange();
    range.selectNodeContents(el);
    range.collapse(false);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
  }, [editing]);

  return (
    <span
      ref={ref as React.RefObject<HTMLSpanElement>}
      className={className}
      contentEditable={editing}
      suppressContentEditableWarning
      onBlur={(event) => {
        if (cancelledRef.current) {
          cancelledRef.current = false;
          return;
        }
        onCommit(event.currentTarget.textContent ?? "");
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          event.currentTarget.blur();
        } else if (event.key === "Escape") {
          event.preventDefault();
          cancelledRef.current = true;
          onCancel?.();
          event.currentTarget.blur();
        }
      }}
    >
      {value}
    </span>
  );
}
