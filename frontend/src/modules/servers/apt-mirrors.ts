export const APT_MIRRORS = ["official", "ustc", "tuna"] as const;

export type AptMirrorId = (typeof APT_MIRRORS)[number];

export function toAptMirror(value: string | null | undefined): AptMirrorId | null {
  if (value === "official" || value === "ustc" || value === "tuna") {
    return value;
  }
  if (value === "tsinghua" || value === "thu") {
    return "tuna";
  }
  return null;
}
