"use client";

import { useState, type ChangeEvent } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Download, LoaderCircle, TriangleAlert, Upload } from "lucide-react";
import {
  exportPluginCatalogAction,
  importPluginCatalogAction,
} from "@/modules/plugins/actions";
import {
  CATALOG_ACTION_TONE,
  CATALOG_STRATEGIES,
  type CatalogStrategy,
  type PluginCatalogBundle,
  type PluginCatalogImportRequest,
  type PluginCatalogImportSummary,
} from "@/modules/plugins/types";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";
import { cn } from "@/shared/lib/cn";

type Banner = { readonly tone: "ok" | "warn" | "danger"; readonly text: string };
type CatalogTab = "export" | "import";

function isCatalogStrategy(value: string): value is CatalogStrategy {
  return (CATALOG_STRATEGIES as readonly string[]).includes(value);
}

function isCatalogBundle(value: unknown): value is PluginCatalogBundle {
  if (value == null || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return (
    record.format === "upkk-cs2-plugin-catalog" &&
    typeof record.version === "number" &&
    Array.isArray(record.plugins) &&
    Array.isArray(record.conflicts)
  );
}

function downloadBundle(bundle: PluginCatalogBundle) {
  const stamp = (bundle.exported_at ?? new Date().toISOString())
    .replace(/[:.]/g, "-")
    .slice(0, 19);
  const blob = new Blob([`${JSON.stringify(bundle, null, 2)}\n`], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `cs2-plugin-catalog-${stamp}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function PluginCatalogDialog({
  open,
  canImport,
  onClose,
}: {
  open: boolean;
  canImport: boolean;
  onClose: () => void;
}) {
  const t = useTranslations("plugins");
  const router = useRouter();
  const [tab, setTab] = useState<CatalogTab>("export");
  const [strategy, setStrategy] = useState<CatalogStrategy>("skip");
  const [fileLabel, setFileLabel] = useState("");
  const [bundle, setBundle] = useState<PluginCatalogBundle | null>(null);
  const [pending, setPending] = useState<"export" | "import" | null>(null);
  const [banner, setBanner] = useState<Banner | null>(null);
  const [summary, setSummary] = useState<PluginCatalogImportSummary | null>(
    null,
  );
  const activeTab = canImport ? tab : "export";

  async function onExport() {
    setPending("export");
    setBanner(null);
    const result = await exportPluginCatalogAction();
    setPending(null);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error });
      return;
    }
    downloadBundle(result.data);
    setBanner({
      tone: "ok",
      text:
        result.data.plugins.length === 0
          ? t("catalog.exportEmpty")
          : t("catalog.exportSuccess", {
              count: result.data.plugins.length,
              conflicts: result.data.conflicts.length,
            }),
    });
  }

  async function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    setSummary(null);
    setBundle(null);
    if (!file) {
      setFileLabel("");
      return;
    }
    setFileLabel(file.name);
    try {
      const parsed: unknown = JSON.parse(await file.text());
      if (!isCatalogBundle(parsed)) {
        setBanner({ tone: "danger", text: t("catalog.invalidFile") });
        return;
      }
      setBundle(parsed);
      setBanner(null);
    } catch {
      setBanner({ tone: "danger", text: t("catalog.invalidFile") });
    }
  }

  async function onImport() {
    if (!canImport) {
      setBanner({ tone: "warn", text: t("catalog.importAdminOnly") });
      return;
    }
    if (!bundle) {
      setBanner({ tone: "warn", text: t("catalog.selectFile") });
      return;
    }
    setPending("import");
    setBanner(null);
    const request: PluginCatalogImportRequest = {
      ...bundle,
      conflict_strategy: strategy,
    };
    const result = await importPluginCatalogAction(request);
    setPending(null);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error });
      return;
    }
    setSummary(result.data);
    setBanner({
      tone: result.data.failed > 0 ? "warn" : "ok",
      text: t("catalog.importSummary", {
        imported: result.data.imported,
        updated: result.data.updated,
        skipped: result.data.skipped,
        failed: result.data.failed,
      }),
    });
    router.refresh();
  }

  return (
    <Dialog
      open={open}
      title={t("catalog.title")}
      description={t("catalog.description")}
      closeLabel={t("catalog.close")}
      onClose={onClose}
    >
      <div className="space-y-4">
        {canImport ? (
          <div
            role="tablist"
            aria-label={t("catalog.title")}
            className="flex rounded-md border border-line bg-surface-raised p-0.5"
          >
            {(["export", "import"] as const).map((item) => (
              <button
                key={item}
                type="button"
                role="tab"
                aria-selected={activeTab === item}
                className={cn(
                  "flex-1 rounded-[5px] px-3 py-1.5 text-sm font-medium transition-colors",
                  activeTab === item
                    ? "bg-surface text-fg shadow-sm"
                    : "text-fg-muted hover:text-fg",
                )}
                onClick={() => {
                  setTab(item);
                  setBanner(null);
                }}
              >
                {item === "export"
                  ? t("catalog.exportTitle")
                  : t("catalog.importTitle")}
              </button>
            ))}
          </div>
        ) : null}

        {activeTab === "export" ? (
          <section className="space-y-4" aria-label={t("catalog.exportTitle")}>
            <p className="text-sm text-fg-muted">{t("catalog.exportHelp")}</p>
            <Button
              type="button"
              variant="secondary"
              disabled={pending !== null}
              onClick={() => void onExport()}
            >
              {pending === "export" ? (
                <LoaderCircle className="animate-spin" />
              ) : (
                <Download />
              )}
              {pending === "export"
                ? t("catalog.exporting")
                : t("catalog.exportAction")}
            </Button>
          </section>
        ) : (
          <section className="space-y-4" aria-label={t("catalog.importTitle")}>
            <p className="text-sm text-fg-muted">{t("catalog.importHelp")}</p>
            <div>
              <Label htmlFor="plugin-catalog-file">
                {t("catalog.catalogFile")}
              </Label>
              <input
                id="plugin-catalog-file"
                type="file"
                accept=".json,application/json"
                onChange={(event) => void onFileChange(event)}
                className="block w-full text-sm text-fg-muted file:mr-3 file:rounded-md file:border-0 file:bg-surface-overlay file:px-3 file:py-2 file:text-sm file:font-medium file:text-fg hover:file:bg-surface-raised"
              />
              {fileLabel ? (
                <p className="mt-1.5 truncate text-xs text-fg-subtle">
                  {fileLabel}
                </p>
              ) : null}
            </div>
            <div>
              <Label htmlFor="plugin-catalog-strategy">
                {t("catalog.conflictStrategy")}
              </Label>
              <Select
                id="plugin-catalog-strategy"
                value={strategy}
                onChange={(event) => {
                  if (isCatalogStrategy(event.target.value)) {
                    setStrategy(event.target.value);
                  }
                }}
              >
                <option value="skip">{t("catalog.strategySkip")}</option>
                <option value="update">{t("catalog.strategyUpdate")}</option>
              </Select>
            </div>
            <Button
              type="button"
              disabled={pending !== null}
              onClick={() => void onImport()}
            >
              {pending === "import" ? (
                <LoaderCircle className="animate-spin" />
              ) : (
                <Upload />
              )}
              {pending === "import"
                ? t("catalog.importing")
                : t("catalog.importAction")}
            </Button>
          </section>
        )}

        {banner ? (
          <div
            className={cn(
              "flex items-start gap-2 rounded-md border px-3 py-2 text-sm",
              banner.tone === "ok" && "border-ok/30 bg-ok-muted/40 text-ok",
              banner.tone === "warn" &&
                "border-warn/30 bg-warn-muted/40 text-warn",
              banner.tone === "danger" &&
                "border-danger/30 bg-danger-muted/40 text-danger",
            )}
            role="status"
          >
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{banner.text}</span>
          </div>
        ) : null}

        {summary && activeTab === "import" ? (
          <ul className="space-y-1.5 text-sm">
            {summary.results.map((item) => (
              <li
                key={`${item.kind}-${item.index}-${item.name}`}
                className="flex flex-wrap items-center gap-2"
              >
                <Badge tone={CATALOG_ACTION_TONE[item.action]}>
                  {t(`catalog.result.${item.action}`)}
                </Badge>
                <span className="font-medium text-fg">{item.name}</span>
                {item.message ? (
                  <span className="text-fg-muted">{item.message}</span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </Dialog>
  );
}
