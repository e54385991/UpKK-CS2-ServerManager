export type ExtractRevealHint = {
  readonly destination: string;
  readonly archiveName: string;
  readonly sourceFolder?: string;
  readonly stripSourceFolder: boolean;
  readonly archiveFolders: readonly string[];
};

type RevealedEntry = {
  readonly name: string;
  readonly type: "file" | "directory";
};

function lastSegment(path: string): string | null {
  const part = path.replace(/\\/g, "/").split("/").filter(Boolean).at(-1);
  return part && part !== "." && part !== ".." ? part : null;
}

export function guessExtractedFolderName(
  hint: ExtractRevealHint,
  archiveStemName: string,
): string | null {
  const source = hint.sourceFolder ? lastSegment(hint.sourceFolder) : null;
  if (source && !hint.stripSourceFolder) return source;
  if (hint.stripSourceFolder) return null;
  const topLevel = hint.archiveFolders.filter((item) => !item.includes("/") && lastSegment(item));
  if (topLevel.length === 1) return topLevel[0] ?? null;
  return archiveStemName && archiveStemName !== hint.archiveName ? archiveStemName : null;
}

export function pickRevealedFolder<T extends RevealedEntry>(
  files: readonly T[],
  guessedName: string | null,
): T | null {
  if (!guessedName) return null;
  return files.find((entry) => entry.type === "directory" && entry.name === guessedName) ?? null;
}

export function extractRevealOpenPath(
  destination: string,
  folder: RevealedEntry | null,
): string | null {
  if (!folder) return null;
  const base = destination.replace(/\/+$/, "") || "/";
  return `${base}/${folder.name}`.replace(/\/{2,}/g, "/");
}

export function revealDelayMs(query?: MediaQueryList | { readonly matches: boolean }): number {
  const reduced =
    query ??
    (typeof window !== "undefined"
      ? window.matchMedia("(prefers-reduced-motion: reduce)")
      : { matches: false });
  return reduced.matches ? 0 : 720;
}
