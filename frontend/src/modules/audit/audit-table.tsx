import Link from "next/link";
import type { Route } from "next";
import { getTranslations } from "next-intl/server";
import { ScrollText, ChevronLeft, ChevronRight } from "lucide-react";
import { listAudit, type AuditQuery } from "@/modules/audit/api";
import { statusTone } from "@/modules/audit/types";
import { Card } from "@/shared/ui/card";
import { Badge } from "@/shared/ui/badge";

function formatTime(iso: string | null): string {
  if (!iso) return "—";
  // Deterministic, locale-independent formatting (server-rendered).
  return iso.slice(0, 19).replace("T", " ");
}

function detailsEntries(details: Record<string, unknown>): [string, string][] {
  return Object.entries(details).map(([key, value]) => [
    key,
    typeof value === "string" ? value : JSON.stringify(value),
  ]);
}

export async function AuditTable({ query }: { query: AuditQuery }) {
  const t = await getTranslations("audit");
  const result = await listAudit(query);

  if (!result.ok) {
    const forbidden = result.status === 403;
    return (
      <Card className="border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        {forbidden
          ? t("forbidden")
          : t("fetchError", { status: result.status || "network" })}
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
        <p className="text-sm text-fg-muted">{t("empty")}</p>
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
              <th className="px-4 py-3 font-medium">{t("columns.time")}</th>
              <th className="px-4 py-3 font-medium">{t("columns.category")}</th>
              <th className="px-4 py-3 font-medium">{t("columns.action")}</th>
              <th className="px-4 py-3 font-medium">{t("columns.status")}</th>
              <th className="px-4 py-3 font-medium">{t("columns.user")}</th>
              <th className="px-4 py-3 font-medium">{t("columns.server")}</th>
              <th className="px-4 py-3 font-medium">{t("columns.ip")}</th>
              <th className="px-4 py-3 font-medium">{t("columns.source")}</th>
              <th className="px-4 py-3 font-medium">{t("columns.details")}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((entry) => {
              const tone = statusTone(entry.status);
              const categoryKey = `categories.${entry.category}`;
              const statusKey = `statuses.${entry.status}`;
              const actionKey = `actions.${entry.action}`;
              const details = detailsEntries(entry.details);
              const serverHref =
                entry.serverId != null
                  ? (`/servers/${entry.serverId}` as Route)
                  : null;
              return (
                <tr
                  key={entry.id}
                  className="border-b border-line/60 transition-colors last:border-0 hover:bg-surface-overlay/40"
                >
                  <td className="whitespace-nowrap px-4 py-2.5 font-mono text-xs text-fg-muted">
                    {formatTime(entry.createdAt)}
                  </td>
                  <td className="px-4 py-2.5">
                    <Badge tone="neutral">
                      {t.has(categoryKey) ? t(categoryKey) : entry.category}
                    </Badge>
                  </td>
                  <td className="px-4 py-2.5 text-fg">
                    {t.has(actionKey) ? t(actionKey) : entry.action}
                  </td>
                  <td className="px-4 py-2.5">
                    <Badge tone={tone}>
                      {t.has(statusKey) ? t(statusKey) : entry.status}
                    </Badge>
                  </td>
                  <td className="px-4 py-2.5 text-fg-muted">
                    {entry.actorUsername ?? "—"}
                  </td>
                  <td className="px-4 py-2.5">
                    {serverHref ? (
                      <Link
                        href={serverHref}
                        className="text-primary hover:underline"
                      >
                        #{entry.serverId}
                      </Link>
                    ) : (
                      <span className="text-fg-subtle">—</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-4 py-2.5 font-mono text-xs text-fg-subtle">
                    {entry.ipAddress ?? "—"}
                  </td>
                  <td className="px-4 py-2.5 text-fg-subtle">{entry.source}</td>
                  <td className="px-4 py-2.5">
                    {details.length === 0 ? (
                      <span className="text-fg-subtle">—</span>
                    ) : (
                      <details>
                        <summary className="cursor-pointer text-xs text-fg-muted">
                          {t("columns.details")}
                        </summary>
                        <dl className="mt-2 max-w-sm space-y-1 font-mono text-[11px] text-fg-muted">
                          {details.map(([key, value]) => (
                            <div key={key}>
                              <dt className="inline text-fg-subtle">{key}: </dt>
                              <dd className="inline break-all">{value}</dd>
                            </div>
                          ))}
                        </dl>
                      </details>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between gap-3 border-t border-line px-4 py-3 text-xs text-fg-muted">
        <span className="tabular-nums">
          {t("pageInfo", { from, to, total })}
        </span>
        <div className="flex items-center gap-2">
          <PagerLink
            enabled={hasPrev}
            query={pageQuery(prevOffset)}
            aria-label={t("prev")}
          >
            <ChevronLeft className="size-4" />
          </PagerLink>
          <PagerLink
            enabled={hasNext}
            query={pageQuery(nextOffset)}
            aria-label={t("next")}
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
