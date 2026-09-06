import ReactMarkdown, { type Components } from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { cn } from "@/shared/lib/cn";
import {
  MARKDOWN_SANITIZE_SCHEMA,
  markdownUrlTransform,
  withoutNode,
} from "@/shared/lib/markdown-safety";

/**
 * Full GitHub-Flavored Markdown rendering for operator-authored copy (plugin
 * marketplace descriptions, etc.).
 *
 * `remark-gfm` adds tables, task lists, strikethrough, footnotes, and
 * autolinks; `rehype-raw` keeps the inline HTML that README-style copy relies
 * on (`<div align="center">`, `<img>`, `<br>`, `<details>`); `rehype-sanitize`
 * runs immediately after it so only the GitHub-safe subset survives — script,
 * style, and event handlers never reach the DOM.
 *
 * The component is synchronous and hook-free, so it renders in Server and
 * Client Components alike.
 */

const COMPONENTS: Components = {
  h1: (props) => (
    <h1 {...withoutNode(props)} className="text-base font-semibold text-fg" />
  ),
  h2: (props) => (
    <h2 {...withoutNode(props)} className="text-sm font-semibold text-fg" />
  ),
  h3: (props) => (
    <h3 {...withoutNode(props)} className="text-sm font-semibold text-fg" />
  ),
  h4: (props) => (
    <h4 {...withoutNode(props)} className="text-sm font-medium text-fg" />
  ),
  h5: (props) => (
    <h5 {...withoutNode(props)} className="text-sm font-medium text-fg" />
  ),
  h6: (props) => (
    <h6
      {...withoutNode(props)}
      className="text-xs font-medium tracking-wide text-fg-subtle uppercase"
    />
  ),
  a: (props) => (
    <a
      {...withoutNode(props)}
      target="_blank"
      rel="noreferrer noopener"
      className="text-primary underline-offset-2 hover:underline"
    />
  ),
  strong: (props) => (
    <strong {...withoutNode(props)} className="font-semibold text-fg" />
  ),
  del: (props) => <del {...withoutNode(props)} className="text-fg-subtle" />,
  ul: (props) => (
    <ul
      {...withoutNode(props)}
      className="list-disc space-y-1 pl-5 marker:text-fg-subtle [&_ol]:mt-1 [&_ul]:mt-1"
    />
  ),
  ol: (props) => (
    <ol
      {...withoutNode(props)}
      className="list-decimal space-y-1 pl-5 marker:text-fg-subtle [&_ol]:mt-1 [&_ul]:mt-1"
    />
  ),
  // GFM task-list items carry their own checkbox, so drop the marker.
  li: (props) => <li {...withoutNode(props)} className="has-[input]:list-none" />,
  input: (props) => (
    <input
      {...withoutNode(props)}
      readOnly
      className="mr-1.5 -ml-5 size-3.5 translate-y-0.5 accent-primary"
    />
  ),
  blockquote: (props) => (
    <blockquote
      {...withoutNode(props)}
      className="space-y-2 border-l-2 border-line-strong pl-3 text-fg-subtle"
    />
  ),
  code: (props) => {
    // A fenced block carries the `language-*` class; anything else is inline.
    const fenced =
      typeof props.className === "string" && props.className.includes("language-");
    return (
      <code
        {...withoutNode(props)}
        className={cn(
          "font-mono",
          fenced
            ? "text-xs text-fg-muted"
            : "rounded border border-line bg-surface-overlay px-1 py-0.5 text-[0.85em] text-fg",
        )}
      />
    );
  },
  pre: (props) => (
    <pre
      {...withoutNode(props)}
      className="overflow-x-auto rounded-md border border-line bg-surface-overlay p-3"
    />
  ),
  hr: (props) => <hr {...withoutNode(props)} className="border-line" />,
  table: (props) => (
    <div className="overflow-x-auto rounded-md border border-line">
      <table
        {...withoutNode(props)}
        className="w-full border-collapse text-left text-xs"
      />
    </div>
  ),
  th: (props) => (
    <th
      {...withoutNode(props)}
      className="border-b border-line bg-surface-overlay px-3 py-2 font-medium text-fg"
    />
  ),
  td: (props) => (
    <td {...withoutNode(props)} className="border-b border-line px-3 py-2 align-top" />
  ),
  img: (props) => (
    // Descriptions embed badges and screenshots from arbitrary hosts, which
    // `next/image` cannot serve without a per-host allowlist.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      {...withoutNode(props)}
      alt={props.alt ?? ""}
      loading="lazy"
      referrerPolicy="no-referrer"
      className="inline-block max-w-full rounded-md"
    />
  ),
};

export function Markdown({
  source,
  className,
}: {
  source: string;
  className?: string;
}) {
  if (source.trim() === "") return null;
  return (
    <div className={cn("space-y-3 leading-relaxed break-words", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw, [rehypeSanitize, MARKDOWN_SANITIZE_SCHEMA]]}
        urlTransform={markdownUrlTransform}
        components={COMPONENTS}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
