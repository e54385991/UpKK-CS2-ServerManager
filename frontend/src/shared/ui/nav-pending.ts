const HINT = "[data-testid='link-pending-hint']";

let pendingHref: string | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

function writeDom(href: string | null) {
  if (typeof document === "undefined") return;
  for (const node of document.querySelectorAll(HINT)) {
    if (!(node instanceof HTMLElement)) continue;
    node.setAttribute(
      "data-pending",
      href != null && node.dataset.pendingHref === href ? "true" : "false",
    );
  }
}

export function markLinkPending(href: string) {
  pendingHref = href;
  writeDom(href);
  emit();
}

export function clearLinkPending(href?: string) {
  if (href != null && pendingHref !== href) return;
  pendingHref = null;
  writeDom(null);
  emit();
}

export function subscribeLinkPending(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getLinkPendingHref() {
  return pendingHref;
}
