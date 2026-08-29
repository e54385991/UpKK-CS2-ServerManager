"use client";

import { useMemo, useState, type ChangeEvent } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  Download,
  LoaderCircle,
  Shield,
  TriangleAlert,
  Upload,
} from "lucide-react";
import {
  exportServerConfigsAction,
  importServerConfigsAction,
} from "@/modules/servers/actions";
import {
  CONFLICT_STRATEGIES,
  IMPORT_ACTION_TONE,
  type ConflictStrategy,
  type ServerConfigBundle,
  type ServerConfigImportRequest,
  type ServerConfigImportSummary,
  type TransferServerOption,
} from "@/modules/servers/types";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";
import { Switch } from "@/shared/ui/switch";
import { cn } from "@/shared/lib/cn";

type Banner = { readonly tone: "ok" | "warn" | "danger"; readonly text: string };
type TransferTab = "export" | "import";

function isConflictStrategy(value: string): value is ConflictStrategy {
  return (CONFLICT_STRATEGIES as readonly string[]).includes(value);
}

function isPortableBundle(value: unknown): value is ServerConfigBundle {
  if (value == null || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return (
    record.format === "upkk-cs2-server-config" &&
    typeof record.version === "number" &&
    Array.isArray(record.servers) &&
    record.servers.length > 0
  );
}

function downloadBundle(bundle: ServerConfigBundle) {
  const stamp = (bundle.exported_at ?? new Date().toISOString())
    .replace(/[:.]/g, "-")
    .slice(0, 19);
  const blob = new Blob([`${JSON.stringify(bundle, null, 2)}\n`], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `cs2-server-config-${stamp}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ServerTransferDialog({
  open,
  servers,
  onClose,
}: {
  open: boolean;
  servers: readonly TransferServerOption[];
  onClose: () => void;
}) {
  const t = useTranslations("servers");
  const router = useRouter();
  const [tab, setTab] = useState<TransferTab>("export");
  const [selected, setSelected] = useState<number[]>(() =>
    servers.map((server) => server.id),
  );
  const [includeSecrets, setIncludeSecrets] = useState(false);
  const [strategy, setStrategy] = useState<ConflictStrategy>("skip");
  const [fileLabel, setFileLabel] = useState("");
  const [bundle, setBundle] = useState<ServerConfigBundle | null>(null);
  const [pending, setPending] = useState<"export" | "import" | null>(null);
  const [banner, setBanner] = useState<Banner | null>(null);
  const [summary, setSummary] = useState<ServerConfigImportSummary | null>(null);

  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const allSelected =
    servers.length > 0 && selected.length === servers.length;

  function toggleServer(id: number) {
    setSelected((current) =>
      current.includes(id)
        ? current.filter((item) => item !== id)
        : [...current, id],
    );
  }

  async function onExport() {
    if (servers.length === 0) {
      setBanner({ tone: "warn", text: t("transfer.noServers") });
      return;
    }
    if (selected.length === 0) {
      setBanner({ tone: "warn", text: t("transfer.selectServer") });
      return;
    }
    setPending("export");
    setBanner(null);
    const result = await exportServerConfigsAction({
      serverIds: selected,
      includeSecrets,
    });
    setPending(null);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error });
      return;
    }
    downloadBundle(result.data);
    setBanner({
      tone: includeSecrets ? "warn" : "ok",
      text: includeSecrets
        ? t("transfer.exportSuccess", { count: selected.length })
        : t("transfer.exportRedactedSuccess", { count: selected.length }),
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
      if (!isPortableBundle(parsed)) {
        setBanner({ tone: "danger", text: t("transfer.invalidFile") });
        return;
      }
      setBundle(parsed);
      setBanner(null);
    } catch {
      setBanner({ tone: "danger", text: t("transfer.invalidFile") });
    }
  }

  async function onImport() {
    if (!bundle) {
      setBanner({ tone: "warn", text: t("transfer.selectFile") });
      return;
    }
    setPending("import");
    setBanner(null);
    const request: ServerConfigImportRequest = {
      ...bundle,
      conflict_strategy: strategy,
    };
    const result = await importServerConfigsAction(request);
    setPending(null);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error });
      return;
    }
    setSummary(result.data);
    setBanner({
      tone: result.data.failed > 0 ? "warn" : "ok",
      text: t("transfer.importSummary", {
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
      title={t("transfer.title")}
      description={t("transfer.description")}
      closeLabel={t("transfer.close")}
      onClose={onClose}
    >
      <div className="space-y-4">
        <div
          role="tablist"
          aria-label={t("transfer.title")}
          className="flex rounded-md border border-line bg-surface-raised p-0.5"
        >
          {(["export", "import"] as const).map((item) => (
            <button
              key={item}
              type="button"
              role="tab"
              aria-selected={tab === item}
              className={cn(
                "flex-1 rounded-[5px] px-3 py-1.5 text-sm font-medium transition-colors",
                tab === item
                  ? "bg-surface text-fg shadow-sm"
                  : "text-fg-muted hover:text-fg",
              )}
              onClick={() => {
                setTab(item);
                setBanner(null);
              }}
            >
              {item === "export"
                ? t("transfer.exportTitle")
                : t("transfer.importTitle")}
            </button>
          ))}
        </div>

        {tab === "export" ? (
          <section className="space-y-4" aria-label={t("transfer.exportTitle")}>
            <p className="text-sm text-fg-muted">{t("transfer.exportHelp")}</p>
            {servers.length > 0 ? (
              <div className="space-y-2">
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setSelected(servers.map((server) => server.id))
                    }
                  >
                    {t("transfer.selectAll")}
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setSelected([])}
                  >
                    {t("transfer.clearSelection")}
                  </Button>
                </div>
                <ul className="max-h-56 space-y-1 overflow-auto rounded-md border border-line p-2">
                  {servers.map((server) => {
                    const checked = selectedSet.has(server.id);
                    const inputId = `export-server-${server.id}`;
                    return (
                      <li key={server.id}>
                        <label
                          htmlFor={inputId}
                          className={cn(
                            "flex cursor-pointer items-center gap-3 rounded-md px-2 py-1.5 text-sm hover:bg-surface-overlay",
                            checked ? "bg-surface-overlay" : undefined,
                          )}
                        >
                          <input
                            id={inputId}
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleServer(server.id)}
                            className="size-4 accent-primary"
                          />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate font-medium text-fg">
                              {server.name}
                            </span>
                            <span className="block truncate font-mono text-xs text-fg-subtle">
                              {server.host}:{server.gamePort}
                            </span>
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
                <p className="text-xs text-fg-subtle">
                  {allSelected
                    ? t("transfer.allSelected", { count: servers.length })
                    : t("transfer.selectedCount", { count: selected.length })}
                </p>
              </div>
            ) : (
              <p className="text-sm text-fg-muted">{t("transfer.noServers")}</p>
            )}

            <div className="flex items-start justify-between gap-3 rounded-md border border-line px-3 py-2.5">
              <div className="space-y-1">
                <p className="text-sm font-medium text-fg">
                  {t("transfer.includeSecrets")}
                </p>
                {includeSecrets ? (
                  <p className="text-xs text-warn">
                    {t("transfer.includeSecretsWarn")}
                  </p>
                ) : null}
              </div>
              <Switch
                id="export-include-secrets"
                checked={includeSecrets}
                onCheckedChange={setIncludeSecrets}
                label={t("transfer.includeSecrets")}
              />
            </div>

            <Button
              type="button"
              variant={includeSecrets ? "outline" : "secondary"}
              disabled={pending !== null || servers.length === 0}
              onClick={() => void onExport()}
            >
              {pending === "export" ? (
                <LoaderCircle className="animate-spin" />
              ) : includeSecrets ? (
                <Download />
              ) : (
                <Shield />
              )}
              {pending === "export"
                ? t("transfer.exporting")
                : includeSecrets
                  ? t("transfer.exportWithSecrets")
                  : t("transfer.exportRedacted")}
            </Button>
          </section>
        ) : (
          <section className="space-y-4" aria-label={t("transfer.importTitle")}>
            <p className="text-sm text-fg-muted">{t("transfer.importHelp")}</p>
            <div>
              <Label htmlFor="server-config-file">
                {t("transfer.configFile")}
              </Label>
              <input
                id="server-config-file"
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
              <Label htmlFor="server-config-strategy">
                {t("transfer.conflictStrategy")}
              </Label>
              <Select
                id="server-config-strategy"
                value={strategy}
                onChange={(event) => {
                  if (isConflictStrategy(event.target.value)) {
                    setStrategy(event.target.value);
                  }
                }}
              >
                <option value="skip">{t("transfer.strategySkip")}</option>
                <option value="update">{t("transfer.strategyUpdate")}</option>
                <option value="rename">{t("transfer.strategyRename")}</option>
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
                ? t("transfer.importing")
                : t("transfer.importAction")}
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

        {summary && tab === "import" ? (
          <ul className="space-y-1.5 text-sm">
            {summary.results.map((item) => (
              <li
                key={`${item.index}-${item.name}`}
                className="flex flex-wrap items-center gap-2"
              >
                <Badge tone={IMPORT_ACTION_TONE[item.action]}>
                  {t(`transfer.result.${item.action}`)}
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
