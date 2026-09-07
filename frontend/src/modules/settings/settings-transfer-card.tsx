"use client";

import { useState, type ChangeEvent } from "react";
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
  exportSettingsAction,
  importSettingsAction,
} from "@/modules/settings/actions";
import type {
  SystemSettingsBundle,
  SystemSettingsImportRequest,
  SystemSettingsImportSummary,
} from "@/modules/settings/types";
import { confirm } from "@/shared/feedback";
import { Button } from "@/shared/ui/button";
import { Label } from "@/shared/ui/input";
import { Switch } from "@/shared/ui/switch";
import { cn } from "@/shared/lib/cn";

type Banner = { readonly tone: "ok" | "warn" | "danger"; readonly text: string };
type TransferTab = "export" | "import";

function isSystemSettingsBundle(value: unknown): value is SystemSettingsBundle {
  if (value == null || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return (
    record.format === "upkk-system-settings" &&
    record.version === 1 &&
    typeof record.include_secrets === "boolean" &&
    record.system != null &&
    typeof record.system === "object" &&
    record.ai != null &&
    typeof record.ai === "object"
  );
}

function downloadBundle(bundle: SystemSettingsBundle) {
  const stamp = (bundle.exported_at ?? new Date().toISOString())
    .replace(/[:.]/g, "-")
    .slice(0, 19);
  const blob = new Blob([`${JSON.stringify(bundle, null, 2)}\n`], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `cs2-system-settings-${bundle.include_secrets ? "secrets" : "redacted"}-${stamp}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function SettingsTransferCard() {
  const t = useTranslations("settings");
  const router = useRouter();
  const [tab, setTab] = useState<TransferTab>("export");
  const [includeSecrets, setIncludeSecrets] = useState(false);
  const [fileLabel, setFileLabel] = useState("");
  const [bundle, setBundle] = useState<SystemSettingsBundle | null>(null);
  const [pending, setPending] = useState<TransferTab | null>(null);
  const [banner, setBanner] = useState<Banner | null>(null);
  const [summary, setSummary] =
    useState<SystemSettingsImportSummary | null>(null);

  async function onExport() {
    if (
      includeSecrets &&
      !(await confirm({
        title: t("transfer.includeSecretsConfirmTitle"),
        description: t("transfer.includeSecretsConfirmDescription"),
        confirmLabel: t("transfer.includeSecretsConfirm"),
        cancelLabel: t("transfer.cancel"),
        tone: "danger",
      }))
    ) {
      return;
    }
    setPending("export");
    setBanner(null);
    const result = await exportSettingsAction(includeSecrets);
    setPending(null);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error });
      return;
    }
    downloadBundle(result.data);
    setBanner({
      tone: includeSecrets ? "warn" : "ok",
      text: includeSecrets
        ? t("transfer.exportSuccessWithSecrets")
        : t("transfer.exportSuccessRedacted"),
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
      if (!isSystemSettingsBundle(parsed)) {
        setBanner({ tone: "danger", text: t("transfer.invalidFile") });
        return;
      }
      setBundle(parsed);
      setIncludeSecrets(parsed.include_secrets);
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
    if (
      !(await confirm({
        title: t("transfer.importConfirmTitle"),
        description: bundle.include_secrets
          ? t("transfer.importConfirmWithSecrets")
          : t("transfer.importConfirmRedacted"),
        confirmLabel: t("transfer.importConfirm"),
        cancelLabel: t("transfer.cancel"),
        tone: "danger",
      }))
    ) {
      return;
    }
    setPending("import");
    setBanner(null);
    const request: SystemSettingsImportRequest = { ...bundle };
    const result = await importSettingsAction(request);
    setPending(null);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error });
      return;
    }
    setSummary(result.data);
    setBanner({
      tone: result.data.ai_enabled_without_key ? "warn" : "ok",
      text: result.data.ai_enabled_without_key
        ? `${result.data.message} ${t("transfer.aiDisabledWithoutKey")}`
        : result.data.message,
    });
    router.refresh();
  }

  return (
    <div
      data-testid="settings-transfer-card"
      className="space-y-4 rounded-lg border border-line bg-surface p-5 shadow-panel"
    >
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
              id="settings-transfer-include-secrets"
              checked={includeSecrets}
              onCheckedChange={setIncludeSecrets}
              label={t("transfer.includeSecrets")}
            />
          </div>
          <Button
            type="button"
            variant={includeSecrets ? "outline" : "secondary"}
            disabled={pending !== null}
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
            <Label htmlFor="settings-transfer-file">
              {t("transfer.settingsFile")}
            </Label>
            <input
              id="settings-transfer-file"
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
        <p className="text-xs text-fg-muted">
          {t("transfer.importSummary", {
            updated: summary.updated_fields?.length ?? 0,
            imported: summary.imported_secret_fields?.length ?? 0,
            preserved: summary.preserved_secret_fields?.length ?? 0,
          })}
        </p>
      ) : null}
    </div>
  );
}
