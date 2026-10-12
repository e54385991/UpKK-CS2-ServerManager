// This browser subscription contains only the public pathname. Server renders
// always use a null snapshot and never publish request state to this module.
let pathname: string | null = null;
const listeners = new Set<() => void>();

export function getNavigationPathname() {
  return pathname;
}

export function publishNavigationPathname(next: string | null) {
  if (pathname === next) return;
  pathname = next;
  for (const listener of listeners) listener();
}

export function subscribeNavigationPathname(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
