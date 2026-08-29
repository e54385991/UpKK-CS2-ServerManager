/**
 * Linux username for the CS2 service account.
 * HTML `pattern` is compiled with the `v` flag; `-` must be escaped there.
 */
export const CS2_USERNAME_PATTERN = "[a-z_][a-z0-9_\\-]*";
export const CS2_USERNAME_RE = /^[a-z_][a-z0-9_-]*$/;

export function isCs2Username(value: string): boolean {
  return CS2_USERNAME_RE.test(value);
}
