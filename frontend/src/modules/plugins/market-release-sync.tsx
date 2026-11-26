"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useTranslations } from "next-intl";
import {
  deployMarketRelease,
  listMarketReleaseTargets,
  previewMarketRelease,
  saveMarketRelease,
} from "@/modules/plugins/market-release-actions";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import type {
  MarketReleasePreviewRequestDto,
  MarketReleasePreviewViewDto,
  MarketReleaseTargetsViewDto,
} from "@/shared/api/types";
import { randomId } from "@/shared/lib/random-id";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Input, Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";

export function MarketReleaseSyncButton({
  pluginId,
  pluginTitle,
}: {
  pluginId: number;
  pluginTitle: string;
}) {
  const t = useTranslations("plugins.releaseSync");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<MarketReleasePreviewViewDto | null>(null);
  const [rule, setRule] = useState("");
  const [targets, setTargets] = useState<MarketReleaseTargetsViewDto | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [queued, setQueued] = useState<number | null>(null);

  async function loadPreview(input: MarketReleasePreviewRequestDto = {}) {
    setBusy(true);
    setError(null);
    setTargets(null);
    setQueued(null);
    const result = await previewMarketRelease(pluginId, input);
    setBusy(false);
    if (!result.ok) {
      setPreview(null);
      setError(result.error);
      return;
    }
    setPreview(result.data);
    setRule(result.data.rule ?? "");
  }

  function showTargets(result: MarketReleaseTargetsViewDto) {
    setTargets(result);
    setSelected([]);
    setRequestId(null);
    setQueued(null);
    setError(null);
  }

  async function save() {
    if (!preview?.preview_id) return;
    setBusy(true);
    setError(null);
    const result = await saveMarketRelease(pluginId, preview.preview_id);
    setBusy(false);
    if (result.ok) showTargets(result.data);
    else setError(result.error);
  }

  async function reopenTargets() {
    setBusy(true);
    setError(null);
    const result = await listMarketReleaseTargets(pluginId);
    setBusy(false);
    if (result.ok) showTargets(result.data);
    else setError(result.error);
  }

  async function deploy() {
    if (!targets || !selected.length) return;
    const id = requestId ?? randomId();
    setRequestId(id);
    setBusy(true);
    setError(null);
    const result = await deployMarketRelease(pluginId, {
      revision: targets.revision,
      request_id: id,
      server_ids: selected,
    });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    const accepted = result.data.filter((item) => item.operation);
    for (const item of accepted) {
      if (item.operation) {
        trackQueuedOperation(item.operation, { serverName: item.serverName ?? undefined });
      }
    }
    const failures = result.data.filter((item) => item.error);
    setQueued(accepted.length);
    setError(failures.map((item) => `${item.serverName ?? item.serverId} · ${item.error}`).join("\n") || null);
    setSelected(failures.map((item) => item.serverId));
    setTargets({
      ...targets,
      targets: targets.targets.filter((target) => !accepted.some((item) => item.serverId === target.server_id)),
    });
    setRequestId(null);
    if (accepted.length && !failures.length) setOpen(false);
  }

  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="outline"
        data-testid="market-release-sync-open"
        onClick={() => {
          setOpen(true);
          void loadPreview();
        }}
      >
        <RefreshCw /> {t("open")}
      </Button>
      <Dialog
        open={open}
        title={t("title", { title: pluginTitle })}
        closeLabel={t("close")}
        className="max-w-2xl"
        onClose={() => { if (!busy) setOpen(false); }}
      >
        <div className="space-y-4" aria-busy={busy}>
          {error ? <p role="alert" className="whitespace-pre-line text-sm text-danger">{error}</p> : null}
          {busy ? <p role="status" className="text-sm text-fg-muted">{t("working")}</p> : null}
          {targets ? (
            <>
              <p className="text-sm font-medium">{t("saved", { tag: targets.tag })}</p>
              {queued != null ? <p role="status" className="text-sm">{t("queued", { count: queued })}</p> : null}
              {targets.targets.length ? (
                <fieldset className="max-h-64 space-y-2 overflow-y-auto">
                  <legend className="mb-2 text-sm">{t("servers")}</legend>
                  {targets.targets.map((target) => (
                    <label key={target.server_id} className="flex items-start gap-2 rounded border border-line p-2 text-sm">
                      <input
                        type="checkbox"
                        className="mt-1 size-4 accent-primary"
                        checked={selected.includes(target.server_id)}
                        disabled={busy || (!selected.includes(target.server_id) && selected.length >= 50)}
                        onChange={(event) => {
                          const checked = event.target.checked;
                          setSelected((current) => checked ? [...current, target.server_id] : current.filter((id) => id !== target.server_id));
                          setRequestId(null);
                        }}
                      />
                      <span className="min-w-0">
                        <span className="block font-medium">{target.server_name} · {target.installed_version}</span>
                        <span className="block break-all text-xs text-fg-muted">{target.installed_asset_name}</span>
                        <span className="text-xs text-fg-muted">
                          {[target.backup_before_update ? t("backup") : null, target.restart_after_update ? t("restart") : null].filter(Boolean).join(" · ")}
                        </span>
                      </span>
                    </label>
                  ))}
                </fieldset>
              ) : <p className="text-sm text-fg-muted">{t("noTargets")}</p>}
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" disabled={busy} onClick={() => void loadPreview()}>{t("preview")}</Button>
                <Button disabled={busy || !selected.length} onClick={() => void deploy()}>
                  {t("deploy", { count: selected.length })}
                </Button>
              </div>
            </>
          ) : (
            <>
              {preview ? (
                <>
                  <p className="text-sm font-medium">{preview.previous_tag ?? "—"} → {preview.tag}</p>
                  {preview.previous_assets.length ? (
                    <p className="break-all text-xs text-fg-muted">{t("previousAssets")} · {preview.previous_assets.join(", ")}</p>
                  ) : null}
                  <div className="space-y-1">
                    <Label htmlFor="market-sync-asset">{t("assets")}</Label>
                    <Select
                      id="market-sync-asset"
                      value={preview.selected_assets[0] ?? ""}
                      disabled={busy}
                      onChange={(event) => void loadPreview({ asset_name: event.target.value })}
                    >
                      <option value="" disabled>{t("choose")}</option>
                      {preview.assets.map((asset) => (
                        <option key={asset.name} value={asset.name} disabled={!asset.ordinary}>
                          {asset.name}{asset.ordinary ? "" : ` (${t("auxiliary")})`}
                        </option>
                      ))}
                    </Select>
                    {preview.selected_assets.length ? (
                      <p className="break-all text-xs text-fg-muted">{preview.selected_assets.join(", ")}</p>
                    ) : null}
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="market-sync-rule">{t("rule")}</Label>
                    <Input id="market-sync-rule" value={rule} disabled={busy} onChange={(event) => setRule(event.target.value)} />
                  </div>
                  {preview.reason ? <p className="text-sm text-warn">{preview.reason}</p> : null}
                </>
              ) : null}
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" disabled={busy} onClick={() => void loadPreview({ rule: rule.trim() || null })}>
                  {t("preview")}
                </Button>
                <Button variant="outline" disabled={busy} onClick={() => void reopenTargets()}>{t("reopen")}</Button>
                <Button disabled={busy || !preview?.preview_id || rule.trim() !== preview.rule} onClick={() => void save()}>
                  {t("save")}
                </Button>
              </div>
            </>
          )}
        </div>
      </Dialog>
    </>
  );
}
