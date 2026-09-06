import { remark } from "remark";
import remarkGfm from "remark-gfm";
import stripMarkdown from "strip-markdown";

/**
 * Flatten Markdown to a one-line excerpt for clamped previews (marketplace
 * cards, tooltips), where rendered headings, tables, and code fences would eat
 * the few visible lines. Rendering the real thing is `shared/ui/markdown`.
 *
 * Keep this on the server: it pulls the remark pipeline in, and the values it
 * produces are plain strings that cross the RSC boundary for free.
 */
const processor = remark().use(remarkGfm).use(stripMarkdown);

/**
 * `remark-stringify` re-escapes punctuation and encodes characters that could
 * be re-read as Markdown. Neither survives into a plain-text excerpt, and the
 * result is rendered as a React text node, so undoing both is safe.
 */
const ESCAPED = /\\([\\`*_{}[\]()#+\-.!>~|])/g;
const REFERENCE = /&(?:#(\d+)|#[Xx]([\dA-Fa-f]+)|(amp|lt|gt|quot|apos|nbsp));/g;
const NAMED: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
};

function decodeReferences(value: string): string {
  return value.replace(REFERENCE, (match, decimal, hex, name) => {
    if (typeof name === "string") return NAMED[name] ?? match;
    const code = Number.parseInt(
      typeof decimal === "string" ? decimal : String(hex),
      typeof decimal === "string" ? 10 : 16,
    );
    if (!Number.isFinite(code) || code < 0x20 || code > 0x10ffff) return match;
    return String.fromCodePoint(code);
  });
}

export function markdownToPlainText(source: string | null | undefined): string {
  if (!source?.trim()) return "";
  return decodeReferences(String(processor.processSync(source)))
    .replace(ESCAPED, "$1")
    .replace(/\s+/g, " ")
    .trim();
}
