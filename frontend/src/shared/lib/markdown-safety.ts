import { defaultUrlTransform } from "react-markdown";
import { defaultSchema } from "rehype-sanitize";
import type { Options as SanitizeSchema } from "rehype-sanitize";

/**
 * The security boundary for rendered Markdown.
 *
 * `rehype-raw` keeps the inline HTML that README-style copy relies on, so
 * everything it produces is then filtered by `rehype-sanitize` against this
 * schema: GitHub's own allowlist plus the few layout attributes that plugin
 * descriptions actually use. Script, style, and event-handler attributes are
 * not in the allowlist and never reach the DOM.
 */
export const MARKDOWN_SANITIZE_SCHEMA: SanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    "*": [...(defaultSchema.attributes?.["*"] ?? []), "align"],
    img: [
      ...(defaultSchema.attributes?.img ?? []),
      "width",
      "height",
      "loading",
      "referrerPolicy",
    ],
    a: [...(defaultSchema.attributes?.a ?? []), "target", "rel"],
  },
  tagNames: [...(defaultSchema.tagNames ?? []), "details", "summary"],
};

/**
 * react-markdown hands every component override the source hast node. It is
 * not a DOM attribute, so it has to come off before the remaining props are
 * spread onto an element — otherwise it renders as `node="[object Object]"`.
 */
export function withoutNode<Props extends object>(
  props: Props,
): Omit<Props, "node"> {
  const rest = { ...props } as Record<string, unknown>;
  delete rest.node;
  return rest as Omit<Props, "node">;
}

const SAFE_PROTOCOL = /^(?:https?:|mailto:|#|\/)/i;

/**
 * Sanitizing already drops unsafe destinations; this keeps the accepted
 * schemes explicit and independent of the schema's defaults.
 */
export function markdownUrlTransform(url: string): string {
  const value = defaultUrlTransform(url);
  return SAFE_PROTOCOL.test(value) ? value : "";
}
