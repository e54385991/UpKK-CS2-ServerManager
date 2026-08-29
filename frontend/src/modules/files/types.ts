export const TEXT_EXTENSIONS = [
  ".txt",
  ".log",
  ".cfg",
  ".conf",
  ".ini",
  ".json",
  ".jsonc",
  ".xml",
  ".yml",
  ".yaml",
  ".sh",
  ".bash",
  ".md",
  ".html",
  ".css",
  ".js",
  ".py",
  ".php",
  ".c",
  ".cpp",
  ".h",
  ".hpp",
  ".java",
  ".cs",
  ".go",
  ".rs",
  ".lua",
  ".sql",
  ".env",
  ".gitignore",
  ".properties",
  ".toml",
  ".vdf",
] as const;

export const ARCHIVE_EXTENSIONS = [
  ".tar.zstd",
  ".tar.lzma",
  ".tar.zst",
  ".tar.bz2",
  ".tar.gz",
  ".tar.xz",
  ".tzst",
  ".tbz2",
  ".tgz",
  ".txz",
  ".tlz",
  ".tbz",
  ".zip",
  ".7z",
  ".rar",
  ".tar",
  ".zstd",
  ".lzma",
  ".zst",
  ".gz",
  ".bz2",
  ".xz",
] as const;

export const ARCHIVE_FORMATS_LABEL =
  "zip, 7z, rar, tar, tar.gz, tgz, tar.bz2, tar.xz, tar.zst, gz, bz2, xz, zst, lzma";

export type FileKind = "file" | "directory";

export type FileEntry = {
  readonly name: string;
  readonly path: string;
  readonly type: FileKind;
  readonly size: number;
  readonly modified: number;
  readonly permissions: string;
  readonly isSymlink: boolean;
};

export type FilesWorkspace = {
  readonly serverId: number;
  readonly root: string;
  readonly path: string;
  readonly sshOk: boolean;
  readonly sshError: string | null;
  readonly files: readonly FileEntry[];
  readonly message: string | null;
};

export type FileContent = {
  readonly path: string;
  readonly content: string;
};

export type FileMutation = {
  readonly success: boolean;
  readonly message: string;
  readonly path: string | null;
};

export type FileDownloadTicket = {
  readonly ticket: string;
  readonly expiresIn: number;
  readonly path: string;
};

export type FileTask = {
  readonly taskId: string;
  readonly status: string;
  readonly message: string | null;
  readonly error: string | null;
  readonly targetPath: string | null;
  readonly destination: string | null;
  readonly elapsedSeconds: number | null;
};

export type FileArchiveInspect = {
  readonly archiveType: string;
  readonly folders: readonly string[];
  readonly entryCount: number;
};

export function isTextFile(name: string): boolean {
  const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
  return (TEXT_EXTENSIONS as readonly string[]).includes(ext);
}

export function isArchiveFile(name: string): boolean {
  const lower = name.toLowerCase();
  return ARCHIVE_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

export function archiveExtensionLabel(name: string): string {
  const lower = name.toLowerCase();
  const match = ARCHIVE_EXTENSIONS.find((ext) => lower.endsWith(ext));
  return match ? match.slice(1) : "archive";
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export type FileKindFilter = "all" | "folders" | "files" | "archives" | "text";
export type FileSortKey = "name" | "size" | "modified";
export type FileSortDir = "asc" | "desc";

export const FILE_KIND_FILTERS = [
  "all",
  "folders",
  "files",
  "archives",
  "text",
] as const satisfies readonly FileKindFilter[];

export function listEntries(files: readonly FileEntry[]): FileEntry[] {
  return files.filter((entry) => entry.name !== "." && entry.name !== "..");
}

export function queryTokens(query: string): string[] {
  return query.trim().toLowerCase().split(/\s+/).filter(Boolean);
}

export function matchesFileQuery(entry: FileEntry, query: string): boolean {
  const tokens = queryTokens(query);
  if (tokens.length === 0) return true;
  const haystack = entry.name.toLowerCase();
  return tokens.every((token) => haystack.includes(token));
}

export function matchesKindFilter(entry: FileEntry, kind: FileKindFilter): boolean {
  if (kind === "all") return true;
  if (kind === "folders") return entry.type === "directory";
  if (kind === "files") return entry.type === "file";
  if (kind === "archives") return entry.type === "file" && isArchiveFile(entry.name);
  return entry.type === "file" && isTextFile(entry.name);
}

export function compareEntries(
  left: FileEntry,
  right: FileEntry,
  key: FileSortKey,
  dir: FileSortDir,
): number {
  if (left.type !== right.type) return left.type === "directory" ? -1 : 1;
  const sign = dir === "asc" ? 1 : -1;
  if (key === "size") return (left.size - right.size) * sign;
  if (key === "modified") return (left.modified - right.modified) * sign;
  return left.name.localeCompare(right.name, undefined, { sensitivity: "base" }) * sign;
}

export function filterAndSortEntries(
  files: readonly FileEntry[],
  query: string,
  kind: FileKindFilter,
  sortKey: FileSortKey,
  sortDir: FileSortDir,
): FileEntry[] {
  return listEntries(files)
    .filter((entry) => matchesKindFilter(entry, kind) && matchesFileQuery(entry, query))
    .sort((left, right) => compareEntries(left, right, sortKey, sortDir));
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
