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
  ".zip",
  ".7z",
  ".tar",
  ".tar.gz",
  ".tgz",
  ".tar.bz2",
  ".tbz2",
  ".tar.xz",
  ".txz",
  ".gz",
  ".bz2",
] as const;

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

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}
