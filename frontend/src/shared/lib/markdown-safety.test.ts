import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import {
  MARKDOWN_SANITIZE_SCHEMA,
  markdownUrlTransform,
  withoutNode,
} from "./markdown-safety.ts";

/**
 * Renders through the exact plugin chain `shared/ui/markdown` uses, minus the
 * styling overrides, so the sanitize boundary is asserted rather than assumed.
 */
function render(source: string): string {
  return renderToStaticMarkup(
    createElement(ReactMarkdown, {
      remarkPlugins: [remarkGfm],
      rehypePlugins: [rehypeRaw, [rehypeSanitize, MARKDOWN_SANITIZE_SCHEMA]],
      urlTransform: markdownUrlTransform,
      children: source,
    }),
  );
}

test("markdownUrlTransform keeps safe destinations", () => {
  assert.equal(
    markdownUrlTransform("https://github.com/a/b"),
    "https://github.com/a/b",
  );
  assert.equal(markdownUrlTransform("mailto:ops@example.com"), "mailto:ops@example.com");
  assert.equal(markdownUrlTransform("/plugins/1"), "/plugins/1");
  assert.equal(markdownUrlTransform("#install"), "#install");
});

test("markdownUrlTransform drops script and data destinations", () => {
  assert.equal(markdownUrlTransform("javascript:alert(1)"), "");
  assert.equal(markdownUrlTransform("data:text/html;base64,PHNjcmlwdD4="), "");
  assert.equal(markdownUrlTransform("file:///etc/passwd"), "");
});

test("a script tag in the source never reaches the output", () => {
  const html = render("before\n\n<script>alert(1)</script>\n\nafter");
  assert.doesNotMatch(html, /<script/);
  assert.doesNotMatch(html, /alert\(1\)/);
  assert.match(html, /before/);
  assert.match(html, /after/);
});

test("event-handler attributes are stripped from raw HTML", () => {
  const html = render('<img src="https://img.example/b.svg" onerror="alert(1)">');
  assert.doesNotMatch(html, /onerror/i);
  assert.match(html, /<img[^>]*src="https:\/\/img\.example\/b\.svg"/);
});

test("a javascript: link renders without an href", () => {
  const html = render("[click](javascript:alert(1))");
  assert.doesNotMatch(html, /javascript:/i);
  assert.match(html, /click/);
});

test("safe raw HTML that README copy relies on survives", () => {
  const html = render('<div align="center"><b>Featured</b><br>plugin</div>');
  assert.match(html, /<div align="center">/);
  assert.match(html, /<b>Featured<\/b>/);
  assert.match(html, /<br\/?>/);
});

test("GFM tables, task lists, and strikethrough render", () => {
  const html = render(
    ["| a | b |", "| --- | --- |", "| 1 | 2 |", "", "- [x] done", "- ~~gone~~"].join("\n"),
  );
  assert.match(html, /<table>/);
  assert.match(html, /<th>a<\/th>/);
  assert.match(html, /type="checkbox"/);
  assert.match(html, /<del>gone<\/del>/);
});

test("images render as real images", () => {
  const html = render("![build passing](https://img.example/badge.svg)");
  assert.match(html, /<img src="https:\/\/img\.example\/badge\.svg" alt="build passing"/);
});

test("withoutNode drops the hast node and keeps the DOM props", () => {
  const result = withoutNode({
    node: { type: "element", tagName: "a" },
    href: "https://example.com",
    children: "x",
  });
  assert.equal("node" in result, false);
  assert.deepEqual(result, { href: "https://example.com", children: "x" });
});

test("a component override spreading withoutNode leaks no node attribute", () => {
  // react-markdown hardcodes `passNode`, so every override in shared/ui/markdown
  // must filter it or React renders node="[object Object]".
  const html = renderToStaticMarkup(
    createElement(ReactMarkdown, {
      components: {
        a: (props) => createElement("a", withoutNode(props)),
        p: (props) => createElement("p", withoutNode(props)),
      },
      children: "[x](https://example.com)",
    }),
  );
  assert.doesNotMatch(html, /\snode=/);
  assert.match(html, /<a href="https:\/\/example\.com">x<\/a>/);
});
