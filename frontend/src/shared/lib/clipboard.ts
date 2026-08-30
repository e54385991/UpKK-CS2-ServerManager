/**
 * Copy text in the browser.
 *
 * `navigator.clipboard` is missing or throws on HTTP LAN origins (typical
 * 1Panel). Awaiting it first also burns the user-gesture turn, so
 * `document.execCommand("copy")` must run synchronously before any `await`.
 */
export async function copyText(value: string): Promise<boolean> {
  const text = value;
  if (copyTextFallback(text)) return true;
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      return false;
    }
  }
  return false;
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
  area.style.width = "8rem";
  area.style.height = "2rem";
  area.style.padding = "0";
  area.style.border = "none";
  area.style.opacity = "0.01";
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

export function selectElementText(
  el: HTMLElement | null,
  doc: Pick<Document, "createRange"> | undefined = typeof document === "undefined"
    ? undefined
    : document,
  selection: Pick<Selection, "removeAllRanges" | "addRange"> | null = typeof window ===
    "undefined"
    ? null
    : window.getSelection(),
): boolean {
  if (!el || !doc || !selection) return false;
  const range = doc.createRange();
  range.selectNodeContents(el);
  selection.removeAllRanges();
  selection.addRange(range);
  return true;
}
