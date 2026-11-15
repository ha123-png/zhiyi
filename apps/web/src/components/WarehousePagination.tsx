import { useState, type ReactNode } from "react";
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";
import { Icon } from "./Icon";
import "./warehouse-pagination.css";

export function WarehousePagination({ page, pageCount, onChange, summary, kind = "table", disabled = false }: {
  page: number;
  pageCount: number;
  onChange: (page: number) => void;
  summary: ReactNode;
  kind?: "table" | "sources";
  disabled?: boolean;
}) {
  const [jump, setJump] = useState("");
  const target = Number(jump);
  const canJump = !disabled && Number.isInteger(target) && target >= 1 && target <= pageCount && target !== page;
  const firstNumber = Math.max(1, Math.min(page - 1, pageCount - 2));
  const pageNumbers = Array.from({ length: Math.min(3, pageCount) }, (_, index) => firstNumber + index);
  const suffix = kind === "sources" ? "来源" : "";
  function change(next: number) {
    if (disabled || next < 1 || next > pageCount || next === page) return;
    setJump("");
    onChange(next);
  }
  return <div className="table-footer warehouse-pagination">
    <span className="footer-info">{summary}</span>
    <nav className="pagination" aria-label={kind === "sources" ? "来源分页" : "数据表分页"}>
      <div className="warehouse-page-buttons">
        <button type="button" className="btn icon-only sm warehouse-page-edge" disabled={disabled || page <= 1}
          title={`第一页${suffix}`} aria-label={`第一页${suffix}`} onClick={() => change(1)}><Icon icon={ChevronsLeft} size={14} /></button>
        <button type="button" className="btn icon-only sm" disabled={disabled || page <= 1}
          title={`上一页${suffix}`} aria-label={`上一页${suffix}`} onClick={() => change(page - 1)}><Icon icon={ChevronLeft} size={14} /></button>
        {pageNumbers.map(number => <button key={number} type="button" disabled={disabled}
          className={`btn sm warehouse-page-number${number === page ? " active" : ""}`} aria-current={number === page ? "page" : undefined}
          onClick={() => change(number)}>{number}</button>)}
        <button type="button" className="btn icon-only sm" disabled={disabled || page >= pageCount}
          title={`下一页${suffix}`} aria-label={`下一页${suffix}`} onClick={() => change(page + 1)}><Icon icon={ChevronRight} size={14} /></button>
        <button type="button" className="btn icon-only sm warehouse-page-edge" disabled={disabled || page >= pageCount}
          title={`最后一页${suffix}`} aria-label={`最后一页${suffix}`} onClick={() => change(pageCount)}><Icon icon={ChevronsRight} size={14} /></button>
      </div>
      <form className="pagination-jump" onSubmit={event => { event.preventDefault(); if (canJump) change(target); }}>
        <span className="warehouse-page-copy">第</span><input type="number" min={1} max={pageCount} step={1} value={jump} placeholder={String(page)}
          aria-label={`跳转页码${suffix}`} disabled={disabled || pageCount <= 1} onChange={event => setJump(event.target.value)} />
        <span><span className="warehouse-page-copy">页 </span>/ <span className="warehouse-page-copy">共 </span>{pageCount}<span className="warehouse-page-copy"> 页</span></span>
        <button type="submit" className="btn sm secondary" disabled={!canJump}>跳转</button>
      </form>
    </nav>
  </div>;
}
