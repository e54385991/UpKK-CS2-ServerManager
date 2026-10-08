type PendingFragment = { locale: string; path: string; hash: string };
let pending: PendingFragment | null = null;

/** Public URL state survives a locale root remount, then is consumed once. */
export function rememberNavigationFragment(value: PendingFragment) {
  pending = value;
}
export function takeNavigationFragment(locale: string) {
  if (!pending || pending.locale !== locale) return null;
  const value = pending;
  pending = null;
  return value;
}
