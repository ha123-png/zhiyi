import { useEffect, useLayoutEffect, useRef, type KeyboardEvent, type RefObject } from "react";

function focusableElements(dialog: HTMLElement) {
  return Array.from(dialog.querySelectorAll<HTMLElement>(
    'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [contenteditable="true"], [tabindex]',
  )).filter(element => element.tabIndex >= 0
    && !element.closest('[hidden], [inert], [aria-hidden="true"], .visually-hidden')
    && getComputedStyle(element).display !== "none"
    && getComputedStyle(element).visibility !== "hidden");
}

/** Keep keyboard navigation in an open modal, and return to its opener on close. */
export function useModalFocus(open: boolean, onClose: () => void, busy = false, initialFocus?: RefObject<HTMLElement | null>) {
  const ref = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const wasOpen = useRef(false);

  useLayoutEffect(() => {
    if (open) {
      if (!wasOpen.current) {
        returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        wasOpen.current = true;
      }
      const dialog = ref.current;
      (initialFocus?.current ?? (dialog && focusableElements(dialog)[0]) ?? dialog)?.focus({ preventScroll: true });
      return;
    }
    if (wasOpen.current) {
      wasOpen.current = false;
      const target = returnFocus.current;
      returnFocus.current = null;
      if (target?.isConnected && !target.closest("[inert]")) target.focus({ preventScroll: true });
    }
  }, [open, initialFocus]);

  useEffect(() => () => {
    const target = returnFocus.current;
    queueMicrotask(() => {
      if (!ref.current?.isConnected && wasOpen.current && target?.isConnected && !target.closest("[inert]")) {
        wasOpen.current = false;
        target.focus({ preventScroll: true });
      }
    });
  }, []);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      if (!busy) onClose();
      return;
    }
    if (event.key !== "Tab" || !ref.current) return;
    const focusable = focusableElements(ref.current);
    if (!focusable.length) {
      event.preventDefault();
      ref.current.focus();
      return;
    }
    const activeIndex = focusable.indexOf(document.activeElement as HTMLElement);
    if (event.shiftKey && activeIndex <= 0) {
      event.preventDefault();
      focusable[focusable.length - 1].focus();
    } else if (!event.shiftKey && (activeIndex === focusable.length - 1 || activeIndex === -1)) {
      event.preventDefault();
      focusable[0].focus();
    }
  }

  return { ref, onKeyDown, tabIndex: -1 };
}
