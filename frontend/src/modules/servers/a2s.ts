export function parseA2SVersion(version: string | null | undefined): string | null {
  if (!version) return null;
  const match = version.match(/(\d+\.\d+\.\d+\.\d+)/);
  return match?.[1] ?? version;
}

export function isA2SVersionOutdated(
  serverVersion: string | null | undefined,
  steamVersion: string | null | undefined,
): boolean {
  const parsed = parseA2SVersion(serverVersion);
  return Boolean(parsed && steamVersion && parsed !== steamVersion);
}

export const A2S_LOG_PAGE_SIZE = 5;

export function paginateA2SLogs<T>(
  items: readonly T[],
  page: number,
  pageSize: number = A2S_LOG_PAGE_SIZE,
): {
  readonly items: readonly T[];
  readonly page: number;
  readonly pageCount: number;
  readonly from: number;
  readonly to: number;
  readonly total: number;
  readonly hasPrev: boolean;
  readonly hasNext: boolean;
} {
  const total = items.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(Math.max(0, page), pageCount - 1);
  const start = safePage * pageSize;
  const slice = items.slice(start, start + pageSize);
  return {
    items: slice,
    page: safePage,
    pageCount,
    from: total === 0 ? 0 : start + 1,
    to: start + slice.length,
    total,
    hasPrev: safePage > 0,
    hasNext: safePage < pageCount - 1 && total > 0,
  };
}

export function formatA2SDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remain = total % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(remain).padStart(2, "0")}`;
  }
  return `${minutes}:${String(remain).padStart(2, "0")}`;
}
