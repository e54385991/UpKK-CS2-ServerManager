/**
 * Copy text in the browser. `navigator.clipboard` is blocked on non-HTTPS
 * LAN origins (typical 1Panel), so fall back to a hidden textarea.
 */
export async function copyText(value: string): Promise<boolean> {
  const text = value;
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Permission denied, insecure context, or a missing user gesture.
    }
  }
  return copyTextFallback(text);
}

export function copyTextFallback(
  text: string,
  doc: Pick<Document, "body" | "createElement" | "execCommand"> | undefined = typeof document ===
    "undefined"
    ? undefined
    : document,
): boolean {
  if (!doc) return false;
  const area = doc.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.setAttribute("aria-hidden", "true");
  area.style.position = "fixed";
  area.style.top = "0";
  area.style.left = "0";
  area.style.width = "1px";
  area.style.height = "1px";
  area.style.padding = "0";
  area.style.border = "none";
  area.style.opacity = "0";
  doc.body.append(area);
  area.focus();
  area.select();
  area.setSelectionRange(0, text.length);
  try {
    return doc.execCommand("copy");
  } catch {
    return false;
  } finally {
    area.remove();
  }
}
