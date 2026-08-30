export const MAX_UPLOAD_FILES = 400;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export type LocalUpload = {
  readonly file: File;
  readonly relativePath: string;
};

export type UploadItemStatus = "queued" | "uploading" | "done" | "error" | "cancelled";

export type UploadItem = {
  readonly id: string;
  readonly name: string;
  readonly relativePath: string;
  readonly size: number;
  readonly loaded: number;
  readonly status: UploadItemStatus;
  readonly error?: string;
};

type FileSystemEntryLike = {
  readonly isFile: boolean;
  readonly isDirectory: boolean;
  readonly name: string;
  file: (ok: (file: File) => void, err?: (error: DOMException) => void) => void;
  createReader: () => {
    readEntries: (
      ok: (entries: FileSystemEntryLike[]) => void,
      err?: (error: DOMException) => void,
    ) => void;
  };
};

export function uploadRelativePath(file: File): string {
  const raw = (file.webkitRelativePath || file.name).replace(/\\/g, "/");
  return raw
    .split("/")
    .filter((part) => part && part !== "." && part !== "..")
    .join("/");
}

export function uploadBasename(relativePath: string): string {
  const parts = relativePath.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts[parts.length - 1] || "upload";
}

export function isSafeUploadRelativePath(relativePath: string): boolean {
  if (!relativePath) return false;
  const parts = relativePath.replace(/\\/g, "/").split("/").filter(Boolean);
  if (parts.length === 0) return false;
  return parts.every((part) => {
    if (part === "." || part === "..") return false;
    for (let index = 0; index < part.length; index += 1) {
      const code = part.charCodeAt(index);
      if (code < 32 || code === 127) return false;
    }
    return true;
  });
}

export function uploadsFromFileList(list: FileList | File[]): LocalUpload[] {
  return Array.from(list).flatMap((file) => {
    const relativePath = uploadRelativePath(file) || file.name;
    if (!isSafeUploadRelativePath(relativePath)) return [];
    return [{ file, relativePath }];
  });
}

export function formatTransferRate(bytesPerSecond: number): string {
  if (!Number.isFinite(bytesPerSecond) || bytesPerSecond <= 0) return "—";
  return `${formatBytes(Math.round(bytesPerSecond))}/s`;
}

export function uploadErrorMessage(text: string, fallback: string): string {
  const trimmed = text.trim();
  if (!trimmed) return fallback;
  try {
    const parsed = JSON.parse(trimmed) as { detail?: unknown; message?: unknown };
    if (typeof parsed.detail === "string" && parsed.detail.trim()) return parsed.detail;
    if (Array.isArray(parsed.detail) && parsed.detail[0]) {
      const first = parsed.detail[0] as { msg?: unknown };
      if (typeof first.msg === "string") return first.msg;
    }
    if (typeof parsed.message === "string" && parsed.message.trim()) return parsed.message;
  } catch {
    /* keep raw text */
  }
  return trimmed;
}

export function toUploadItems(files: readonly LocalUpload[]): UploadItem[] {
  return files.map((item, index) => ({
    id: `${index}-${item.relativePath}`,
    name: item.file.name,
    relativePath: item.relativePath,
    size: item.file.size,
    loaded: 0,
    status: "queued",
  }));
}

async function readAllEntries(
  reader: FileSystemEntryLike["createReader"] extends () => infer R ? R : never,
): Promise<FileSystemEntryLike[]> {
  const all: FileSystemEntryLike[] = [];
  for (;;) {
    const batch = await new Promise<FileSystemEntryLike[]>((resolve, reject) => {
      reader.readEntries(resolve, reject);
    });
    if (batch.length === 0) return all;
    all.push(...batch);
  }
}

async function walkEntry(
  entry: FileSystemEntryLike,
  prefix: string,
  collected: LocalUpload[],
): Promise<void> {
  if (collected.length >= MAX_UPLOAD_FILES) return;
  const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
  if (entry.isFile) {
    if (!isSafeUploadRelativePath(relative)) return;
    try {
      const file = await new Promise<File>((resolve, reject) => {
        entry.file(resolve, reject);
      });
      collected.push({ file, relativePath: relative });
    } catch {
      /* skip unreadable files so one bad entry does not abort the folder */
    }
    return;
  }
  if (!entry.isDirectory) return;
  try {
    const children = await readAllEntries(entry.createReader());
    for (const child of children) {
      if (collected.length >= MAX_UPLOAD_FILES) return;
      await walkEntry(child, relative, collected);
    }
  } catch {
    /* skip unreadable directories */
  }
}

export async function uploadsFromDataTransfer(data: DataTransfer): Promise<LocalUpload[]> {
  const items = Array.from(data.items ?? []);
  const collected: LocalUpload[] = [];
  let walked = false;
  for (const item of items) {
    const getter = (
      item as DataTransferItem & {
        webkitGetAsEntry?: () => FileSystemEntryLike | null;
      }
    ).webkitGetAsEntry;
    const entry = typeof getter === "function" ? getter.call(item) : null;
    if (!entry) continue;
    walked = true;
    try {
      await walkEntry(entry, "", collected);
    } catch {
      /* keep walking other dropped items */
    }
  }
  if (walked) return collected;
  return uploadsFromFileList(data.files);
}

export function uploadFileWithProgress(options: {
  readonly serverId: number;
  readonly destPath: string;
  readonly file: File;
  readonly relativePath?: string;
  readonly signal?: AbortSignal;
  readonly onProgress?: (loaded: number, total: number) => void;
}): Promise<void> {
  const { serverId, destPath, file, relativePath, signal, onProgress } = options;
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const query = new URLSearchParams({ path: destPath });
    if (relativePath) query.set("relative_path", relativePath);
    xhr.open("POST", `/files-upload/servers/${serverId}?${query.toString()}`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded, event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
        return;
      }
      reject(new Error(uploadErrorMessage(xhr.responseText, `HTTP ${xhr.status}`)));
    };
    xhr.onerror = () => reject(new Error("network"));
    xhr.onabort = () => reject(new DOMException("aborted", "AbortError"));
    const abort = () => xhr.abort();
    if (signal) {
      if (signal.aborted) {
        abort();
        return;
      }
      signal.addEventListener("abort", abort, { once: true });
    }
    const body = new FormData();
    body.append("file", file, uploadBasename(relativePath || file.name));
    xhr.send(body);
  });
}
