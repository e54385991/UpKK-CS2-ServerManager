const HINT = "[data-testid='link-pending-hint']";
const CAPTURE_LISTENER = Symbol.for("upkk.link-pending-capture");

let pendingHref: string | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
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
  const captureDocument: Document & { [CAPTURE_LISTENER]?: EventListener } = document;
  const previous = captureDocument[CAPTURE_LISTENER];
  if (previous === onCaptureClick) return;
  if (previous) document.removeEventListener("click", previous, true);
  captureDocument[CAPTURE_LISTENER] = onCaptureClick;
  document.addEventListener("click", onCaptureClick, true);
}

if (typeof document !== "undefined") {
  ensureLinkPendingCapture();
}

export function markLinkPending(href: string) {
  pendingHref = href;
  emit();
}

export function clearLinkPending(href?: string) {
  if (href != null && pendingHref !== href) return;
  pendingHref = null;
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

/** True while click-capture has marked this href, even after the router commits. */
export function isCapturedLinkPending(href: string, pendingHref: string | null) {
  return pendingHref === href;
}
