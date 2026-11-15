import { useLayoutEffect, useState, type RefObject } from "react";

export function useValidationFieldLocator(container: RefObject<HTMLElement | null>, taskId: string | undefined) {
  const [request, setRequest] = useState<{ taskId: string; field: string }>();
  useLayoutEffect(() => {
    if (!request || request.taskId !== taskId) return;
    // Run after React commits the requested detail page, including a jump from another page.
    const field = Array.from(container.current?.querySelectorAll<HTMLElement>("[data-review-field]") ?? [])
      .find(node => node.dataset.reviewField === request.field);
    if (!field) return;
    const editor = field.querySelector<HTMLElement>('[contenteditable="true"]');
    (editor ?? field).focus({ preventScroll: true });
    field.scrollIntoView?.({ block: "center", inline: "nearest" });
  }, [container, request, taskId]);
  return (field: string) => { if (taskId) setRequest({ taskId, field }); };
}
