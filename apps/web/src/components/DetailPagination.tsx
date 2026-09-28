import { useEffect, useState } from "react";

// Keep a whole edit draft intact while bounding the number of mounted inputs.
export function useDetailPage(count: number, resetKey?: string) {
  const [requestedPage, setPage] = useState(1);
  const size = 20;
  const pages = Math.max(1, Math.ceil(count / size));
  const page = Math.min(requestedPage, pages);
  useEffect(() => setPage(1), [resetKey]);
  useEffect(() => setPage(current => Math.min(current, pages)), [pages]);
  return { page, pages, size, start: (page - 1) * size, end: page * size, setPage };
}

export function DetailPagination({ paging, count, disabled = false }: {
  paging: ReturnType<typeof useDetailPage>;
  count: number;
  disabled?: boolean;
}) {
  if (paging.pages <= 1) return null;
  return <nav className="file-revision-pagination" aria-label="明细分页">
    <span className="support">明细 {paging.start + 1}–{Math.min(paging.end, count)} / {count}</span>
    <div className="file-detail-pagination">
      <button className="btn secondary sm" type="button" disabled={disabled || paging.page <= 1}
        onClick={() => paging.setPage(paging.page - 1)}>上一页明细</button>
      <span>{paging.page} / {paging.pages}</span>
      <button className="btn secondary sm" type="button" disabled={disabled || paging.page >= paging.pages}
        onClick={() => paging.setPage(paging.page + 1)}>下一页明细</button>
    </div>
  </nav>;
}
