"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { deleteCleanupAction, scanCleanupAction } from "@/modules/cleanup/actions";
import type { CleanupItem, CleanupScan } from "@/modules/cleanup/types";
import { confirm } from "@/shared/feedback";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Input, Label } from "@/shared/ui/input";
import { Skeleton } from "@/shared/ui/skeleton";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function ItemList({ items, limit = 20 }: { items: readonly CleanupItem[]; limit?: number }) {
  const shown = items.slice(0, limit);
  return (
    <ul className="max-h-40 space-y-1 overflow-auto text-xs text-fg-muted">
      {shown.map((item) => (
        <li key={item.path} className="break-all">
          {item.path} · {formatSize(item.size)}
        </li>
      ))}
      {items.length > limit ? <li>…</li> : null}
    </ul>
  );
}

export function CleanupConsole({ serverId }: { serverId: number }) {
  const t = useTranslations("cleanup");
  const [scan, setScan] = useState<CleanupScan | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [workshopConfirm, setWorkshopConfirm] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  async function runScan() {
    setPending("scan");
    setBanner(null);
    const result = await scanCleanupAction(serverId);
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setScan(result.data);
    setSelected([]);
  }

  function toggleArchive(path: string, checked: boolean) {
    setSelected((current) =>
      checked ? [...current, path] : current.filter((item) => item !== path),
    );
  }

  async function removeSafe() {
    if (!(await confirm(t("confirmSafe")))) return;
    setPending("safe");
    const result = await deleteCleanupAction(serverId, { mode: "safe" });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setBanner(result.data.message);
    setScan(null);
  }

  async function removeArchives() {
    if (selected.length === 0) return;
    if (!(await confirm(t("confirmArchives")))) return;
    setPending("archives");
    const result = await deleteCleanupAction(serverId, {
      mode: "archives",
      paths: selected,
    });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setBanner(result.data.message);
    setScan(null);
    setSelected([]);
  }

  async function removeWorkshop() {
    if (!(await confirm(t("confirmWorkshop")))) return;
    setPending("workshop");
    const result = await deleteCleanupAction(serverId, {
      mode: "workshop",
      confirmationText: workshopConfirm,
    });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setBanner(result.data.message);
    setScan(null);
    setWorkshopConfirm("");
  }

  return (
    <Card data-testid="cleanup-console">
      <CardHeader>
        <div>
          <CardTitle>{t("title")}</CardTitle>
          <CardDescription>{t("help")}</CardDescription>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={Boolean(pending)}
          onClick={() => void runScan()}
        >
          {pending === "scan" ? t("scanning") : t("scan")}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {banner ? (
          <p className="text-sm text-fg-muted" role="status">
            {banner}
          </p>
        ) : null}
        {!scan ? (
          <p className="text-sm text-fg-muted">{t("scanHint")}</p>
        ) : (
          <div className="grid gap-4 lg:grid-cols-3">
            <p className="text-sm text-fg-muted lg:col-span-3">
              {t("total")}: {formatSize(scan.totalSize)}
            </p>
            <section className="space-y-3 rounded-md border border-line p-3">
              <h3 className="text-sm font-medium">{t("safeTitle")}</h3>
              <p className="text-xs text-fg-subtle">{t("safeHelp")}</p>
              <p className="text-xs text-fg-muted">
                {t("items")}: {scan.safeItems.length}
              </p>
              <ItemList items={scan.safeItems} />
              <Button
                type="button"
                size="sm"
                disabled={Boolean(pending) || scan.safeItems.length === 0}
                onClick={() => void removeSafe()}
              >
                {pending === "safe" ? t("deleting") : t("cleanSafe")}
              </Button>
            </section>
            <section className="space-y-3 rounded-md border border-line p-3">
              <h3 className="text-sm font-medium">{t("archivesTitle")}</h3>
              <p className="text-xs text-fg-subtle">{t("archivesHelp")}</p>
              <ul className="max-h-40 space-y-2 overflow-auto text-xs">
                {scan.archiveItems.map((item) => (
                  <li key={item.path} className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={selected.includes(item.path)}
                      onChange={(event) =>
                        toggleArchive(item.path, event.target.checked)
                      }
                    />
                    <span className="break-all text-fg-muted">
                      {item.path} · {formatSize(item.size)}
                    </span>
                  </li>
                ))}
              </ul>
              <Button
                type="button"
                size="sm"
                variant="secondary"
                disabled={Boolean(pending) || selected.length === 0}
                onClick={() => void removeArchives()}
              >
                {pending === "archives" ? t("deleting") : t("deleteArchives")}
              </Button>
            </section>
            <section className="space-y-3 rounded-md border border-danger/30 p-3">
              <h3 className="text-sm font-medium text-danger">{t("workshopTitle")}</h3>
              <p className="text-xs text-fg-subtle">{t("workshopHelp")}</p>
              <p className="break-all font-mono text-xs text-fg-muted">
                {scan.workshopPath || "—"}
              </p>
              <p className="text-xs text-fg-muted">
                {t("items")}: {scan.workshopCount} · {formatSize(scan.workshopSize)}
              </p>
              <div className="space-y-2">
                <Label htmlFor="workshop-confirm">{t("workshopConfirmLabel")}</Label>
                <Input
                  id="workshop-confirm"
                  value={workshopConfirm}
                  onChange={(event) => setWorkshopConfirm(event.target.value)}
                  placeholder="DELETE WORKSHOP"
                />
              </div>
              <Button
                type="button"
                size="sm"
                variant="danger"
                disabled={
                  Boolean(pending) ||
                  scan.workshopCount === 0 ||
                  workshopConfirm !== "DELETE WORKSHOP"
                }
                onClick={() => void removeWorkshop()}
              >
                {pending === "workshop" ? t("deleting") : t("deleteWorkshop")}
              </Button>
            </section>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function CleanupPanelSkeleton() {
  return (
    <div className="max-w-5xl rounded-lg border border-line bg-surface p-5 shadow-panel">
      <Skeleton className="mb-4 h-4 w-40" />
      <Skeleton className="mb-2 h-4 w-72" />
      <Skeleton className="h-48 w-full" />
    </div>
  );
}
