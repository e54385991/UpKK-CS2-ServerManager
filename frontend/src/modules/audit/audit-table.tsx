import Link from "next/link";
import { ScrollText, ChevronLeft, ChevronRight } from "lucide-react";
import { listAudit, type AuditQuery } from "@/modules/audit/api";
import { categoryLabel, statusMeta } from "@/modules/audit/types";
import { Card } from "@/shared/ui/card";
import { Badge } from "@/shared/ui/badge";

function formatTime(iso: string | null): string {
  if (!iso) return "—";
  // Deterministic, locale-independent formatting (server-rendered).
  return iso.slice(0, 19).replace("T", " ");
}

export async function AuditTable({ query }: { query: AuditQuery }) {
  const result = await listAudit(query);

  if (!result.ok) {
    const forbidden = result.status === 403;
    return (
      <Card className="border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        {forbidden
          ? "仅管理员可查看审计日志。"
          : `暂时无法获取审计日志（${result.status || "网络错误"}）。`}
      </Card>
    );
  }

  const { items, total, limit, offset } = result.data;

  if (total === 0) {
    return (
      <Card className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
        <span className="flex size-12 items-center justify-center rounded-full bg-surface-overlay text-fg-subtle">
          <ScrollText className="size-6" />
        </span>
        <p className="text-sm text-fg-muted">没有匹配的审计事件。</p>
      </Card>
    );
  }

  const from = offset + 1;
  const to = Math.min(offset + limit, total);
  const prevOffset = Math.max(0, offset - limit);
  const nextOffset = offset + limit;
  const hasPrev = offset > 0;
  const hasNext = nextOffset < total;

  const pageQuery = (nextOffsetValue: number) => ({
    ...(query.category ? { category: query.category } : {}),
    ...(query.status ? { status: query.status } : {}),
    ...(query.username ? { username: query.username } : {}),
    offset: String(nextOffsetValue),
  });

  return (
    <Card className="overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-fg-subtle">
              <th className="px-4 py-3 font-medium">时间</th>
              <th className="px-4 py-3 font-medium">分类</th>
              <th className="px-4 py-3 font-medium">动作</th>
              <th className="px-4 py-3 font-medium">状态</th>
              <th className="px-4 py-3 font-medium">用户</th>
              <th className="px-4 py-3 font-medium">IP</th>
              <th className="px-4 py-3 font-medium">来源</th>
            </tr>
          </thead>
          <tbody>
            {items.map((entry) => {
              const meta = statusMeta(entry.status);
              return (
                <tr
                  key={entry.id}
                  className="border-b border-line/60 transition-colors last:border-0 hover:bg-surface-overlay/40"
                >
                  <td className="whitespace-nowrap px-4 py-2.5 font-mono text-xs text-fg-muted">
                    {formatTime(entry.createdAt)}
                  </td>
                  <td className="px-4 py-2.5">
                    <Badge tone="neutral">{categoryLabel(entry.category)}</Badge>
                  </td>
                  <td className="px-4 py-2.5 text-fg">{entry.action}</td>
                  <td className="px-4 py-2.5">
                    <Badge tone={meta.tone}>{meta.label}</Badge>
                  </td>
                  <td className="px-4 py-2.5 text-fg-muted">
                    {entry.actorUsername ?? "—"}
                  </td>
                  <td className="whitespace-nowrap px-4 py-2.5 font-mono text-xs text-fg-subtle">
                    {entry.ipAddress ?? "—"}
                  </td>
                  <td className="px-4 py-2.5 text-fg-subtle">{entry.source}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between gap-3 border-t border-line px-4 py-3 text-xs text-fg-muted">
        <span>
          第 <span className="tabular-nums text-fg">{from}</span>–
          <span className="tabular-nums text-fg">{to}</span> / 共{" "}
          <span className="tabular-nums text-fg">{total}</span> 条
        </span>
        <div className="flex items-center gap-2">
          <PagerLink
            enabled={hasPrev}
            query={pageQuery(prevOffset)}
            aria-label="上一页"
          >
            <ChevronLeft className="size-4" />
          </PagerLink>
          <PagerLink
            enabled={hasNext}
            query={pageQuery(nextOffset)}
            aria-label="下一页"
          >
            <ChevronRight className="size-4" />
          </PagerLink>
        </div>
      </div>
    </Card>
  );
}

function PagerLink({
  enabled,
  query,
  children,
  ...rest
}: {
  enabled: boolean;
  query: Record<string, string>;
  children: React.ReactNode;
  "aria-label"?: string;
}) {
  if (!enabled) {
    return (
      <span
        className="flex size-8 items-center justify-center rounded-md border border-line text-fg-subtle opacity-40"
        {...rest}
      >
        {children}
      </span>
    );
  }
  return (
    <Link
      href={{ pathname: "/audit", query }}
      className="flex size-8 items-center justify-center rounded-md border border-line text-fg-muted transition-colors hover:bg-surface-overlay hover:text-fg"
      {...rest}
    >
      {children}
    </Link>
  );
}

export function AuditTableSkeleton() {
  return (
    <Card className="p-4">
      <div className="space-y-2">
        {Array.from({ length: 8 }).map((_, index) => (
          <div
            key={index}
            className="h-9 animate-pulse rounded bg-surface-overlay/60"
          />
        ))}
      </div>
    </Card>
  );
}
