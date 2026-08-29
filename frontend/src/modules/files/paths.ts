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
