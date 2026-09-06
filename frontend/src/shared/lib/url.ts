const SAFE_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);

/**
 * Normalize a URL that came from stored data (a plugin's repository field, a
 * Markdown destination), or return `null` when its scheme is not one the
 * console is willing to hand to the browser. Blocks `javascript:`, `data:`,
 * and protocol-relative `//host` targets.
 */
export function safeUrl(raw: string | null | undefined): string | null {
  const value = raw?.trim() ?? "";
  if (value === "" || value.startsWith("//")) return null;
  if (value.startsWith("/") || value.startsWith("#")) return value;
  try {
    const url = new URL(value);
    return SAFE_PROTOCOLS.has(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}
