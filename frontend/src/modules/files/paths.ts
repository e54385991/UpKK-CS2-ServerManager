export function isMissingPathError(text: string | null | undefined): boolean {
  const value = (text || "").toLowerCase();
  return (
    value.includes("no such file") ||
    value.includes("not a directory") ||
    value.includes("no such path") ||
    value.includes("does not exist")
  );
}

export function normalizeDir(path: string): string {
  return path.replace(/\/+$/, "") || "/";
}

export function parentPath(path: string): string {
  const trimmed = normalizeDir(path);
  const index = trimmed.lastIndexOf("/");
  return index <= 0 ? "/" : trimmed.slice(0, index);
}

export function isAtRoot(root: string, path: string): boolean {
  return normalizeDir(root) === normalizeDir(path);
}

/** Stay inside the game directory; never walk above `root`. */
export function parentWithinRoot(root: string, path: string): string {
  const normalizedRoot = normalizeDir(root);
  const normalized = normalizeDir(path);
  if (normalized === normalizedRoot) return normalizedRoot;
  const parent = parentPath(normalized);
  if (
    normalizedRoot !== "/" &&
    parent !== normalizedRoot &&
    !parent.startsWith(`${normalizedRoot}/`)
  ) {
    return normalizedRoot;
  }
  return parent;
}

export function collapseSlashes(path: string): string {
  return path.replace(/\/{2,}/g, "/");
}

export function resolveJumpPath(root: string, current: string, draft: string): string {
  const trimmed = draft.trim();
  const normalizedRoot = root.replace(/\/+$/, "") || "/";
  if (!trimmed) return normalizedRoot;
  if (trimmed.startsWith("/")) {
    return collapseSlashes(trimmed.replace(/\/+$/, "") || "/");
  }
  const base = (current || root).replace(/\/+$/, "") || "/";
  return collapseSlashes(`${base}/${trimmed}`);
}

export function filesHref(serverId: number, root: string, path: string): string {
  return isAtRoot(root, path)
    ? `/servers/${serverId}/files`
    : `/servers/${serverId}/files?path=${encodeURIComponent(path)}`;
}

/** Sync the address bar without a Next.js navigation (which scrolls to top). */
export function replaceFilesUrl(href: string): void {
  const current = `${window.location.pathname}${window.location.search}`;
  if (current === href) return;
  window.history.replaceState(window.history.state, "", href);
}

export function isPathInsideRoot(root: string, path: string): boolean {
  const normalizedRoot = normalizeDir(root);
  const normalized = normalizeDir(path);
  return (
    normalized === normalizedRoot ||
    (normalizedRoot !== "/" && normalized.startsWith(`${normalizedRoot}/`)) ||
    normalizedRoot === "/"
  );
}

export function joinUnderRoot(root: string, relative: string): string {
  const normalizedRoot = normalizeDir(root);
  const raw = relative.replace(/\\/g, "/");
  const parts = raw.split("/").filter((part) => part && part !== ".");
  if (parts.length === 0 || parts.some((part) => part === "..")) return normalizedRoot;
  return collapseSlashes(`${normalizedRoot}/${parts.join("/")}`);
}

export function relativeFromRoot(root: string, path: string): string | null {
  const normalizedRoot = normalizeDir(root);
  const normalized = normalizeDir(path);
  if (!isPathInsideRoot(normalizedRoot, normalized)) return null;
  if (normalized === normalizedRoot) return "";
  return normalized.slice(normalizedRoot.length + 1);
}

/** Relative to overview ``game_directory`` (SteamCMD installs into ``<root>/cs2``). */
export const COMMON_FILE_SHORTCUTS = [
  { id: "root", relative: "" },
  { id: "cfg", relative: "cs2/game/csgo/cfg" },
  { id: "css", relative: "cs2/game/csgo/addons/counterstrikesharp" },
  { id: "mam", relative: "cs2/game/csgo/addons/multiaddonmanager" },
  { id: "addons", relative: "cs2/game/csgo/addons" },
  { id: "csgo", relative: "cs2/game/csgo" },
] as const;

export function renameSelectionEnd(name: string, isDirectory: boolean): number {
  if (isDirectory) return name.length;
  const dot = name.lastIndexOf(".");
  return dot > 0 ? dot : name.length;
}

export function breadcrumbs(root: string, path: string): { name: string; path: string }[] {
  const normalizedRoot = root.replace(/\/+$/, "") || "/";
  const normalized = path.replace(/\/+$/, "") || normalizedRoot;
  const parts = [
    { name: normalizedRoot.split("/").pop() || normalizedRoot, path: normalizedRoot },
  ];
  if (normalized === normalizedRoot) return parts;
  const rest = normalized.slice(normalizedRoot.length).split("/").filter(Boolean);
  let current = normalizedRoot;
  for (const part of rest) {
    current = `${current}/${part}`;
    parts.push({ name: part, path: current });
  }
  return parts;
}
