const HINT = "[data-testid='link-pending-hint']";
const CAPTURE_ATTR = "data-link-pending-capture";

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

function hrefFromClick(event: Event) {
  const target = event.target;
  if (!(target instanceof Element)) return null;
  const link = target.closest("a");
  if (!(link instanceof HTMLAnchorElement)) return null;
  const hint = link.querySelector(HINT);
  if (!(hint instanceof HTMLElement)) return null;
  return hint.dataset.pendingHref ?? null;
}

function onCaptureClick(event: Event) {
  const href = hrefFromClick(event);
  if (href) markLinkPending(href);
}

export function ensureLinkPendingCapture() {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (root.getAttribute(CAPTURE_ATTR) === "true") return;
  root.setAttribute(CAPTURE_ATTR, "true");
  document.addEventListener("click", onCaptureClick, true);
}

if (typeof document !== "undefined") {
  ensureLinkPendingCapture();
}

export function markLinkPending(href: string) {
  pendingHref = href;
  emit();
  writeDom(href);
}

export function clearLinkPending(href?: string) {
  if (href != null && pendingHref !== href) return;
  pendingHref = null;
  emit();
  writeDom(null);
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
