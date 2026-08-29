/**
 * Set a first-party cookie from the browser. Kept in a plain module (not a
 * component/hook) so the document mutation is outside React's render scope.
 */
export function setCookie(
  name: string,
  value: string,
  maxAgeSeconds = 60 * 60 * 24 * 365,
): void {
  document.cookie = `${name}=${encodeURIComponent(value)}; path=/; max-age=${maxAgeSeconds}; samesite=lax`;
}
