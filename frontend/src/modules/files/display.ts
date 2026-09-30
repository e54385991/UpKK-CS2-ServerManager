import { queryTokens } from "./types.ts";

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export type NamePart = {
  readonly text: string;
  readonly match: boolean;
};

export function highlightName(name: string, query: string): NamePart[] {
  const tokens = [...new Set(queryTokens(query))].sort((left, right) => right.length - left.length);
  if (tokens.length === 0) return [{ text: name, match: false }];

  const marks = new Array<boolean>(name.length).fill(false);
  const lower = name.toLowerCase();
  for (const token of tokens) {
    let from = 0;
    while (from < lower.length) {
      const index = lower.indexOf(token, from);
      if (index === -1) break;
      marks.fill(true, index, index + token.length);
      from = index + token.length;
    }
  }

  const parts: NamePart[] = [];
  let cursor = 0;
  while (cursor < name.length) {
    const match = marks[cursor] === true;
    let end = cursor + 1;
    while (end < name.length && marks[end] === match) end += 1;
    parts.push({ text: name.slice(cursor, end), match });
    cursor = end;
  }
  return parts;
}
