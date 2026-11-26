"use client";

import { useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { LoaderCircle, PackageCheck } from "lucide-react";
import { syncMarketPluginReleasesAction } from "@/modules/plugins/actions";
import { trackReleaseSync } from "@/modules/plugins/release-sync-activity";
import type { PluginFrameworkSection } from "@/modules/plugins/types";
import { notify } from "@/shared/feedback";
import { randomId } from "@/shared/lib/random-id";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";

/** Admin-only bulk release sync button for the plugin marketplace. */
export function SyncReleasesButton({
  framework,
}: {
  framework: PluginFrameworkSection;
}) {
  const t = useTranslations("plugins");
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<"current" | "all">("current");
  const [pending, setPending] = useState(false);
  const requestId = useRef<string | null>(null);

  async function submit() {
    setPending(true);
    const id = (requestId.current ??= randomId());
    const result = await syncMarketPluginReleasesAction({
      requestId: id,
      mode: "sync",
      framework: scope === "all" ? null : framework,
    });
    setPending(false);
    if (!result.ok) {
      notify.error(
        result.status === 403
          ? t("bulkReleaseSync.forbidden")
          : result.error || t("bulkReleaseSync.failed"),
      );
      return;
    }
    requestId.current = null;
    setOpen(false);
    trackReleaseSync(result.data);
    notify.success(t("bulkReleaseSync.submitted", { count: result.data.total }));
  }

  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="secondary"
        data-testid="market-sync-releases"
        title={t("bulkReleaseSync.hint")}
        disabled={pending}
        onClick={() => {
          setScope("current");
          requestId.current = null;
          setOpen(true);
        }}
      >
        {pending ? <LoaderCircle className="animate-spin" /> : <PackageCheck />}
        {pending ? t("bulkReleaseSync.submitting") : t("bulkReleaseSync.open")}
      </Button>
      <Dialog
        open={open}
        title={t("bulkReleaseSync.title")}
        closeLabel={t("bulkReleaseSync.close")}
        onClose={() => {
          if (!pending) setOpen(false);
        }}
      >
        <div className="space-y-4">
          <p className="text-sm text-fg-muted">{t("bulkReleaseSync.description")}</p>
          <div className="space-y-1.5">
            <Label htmlFor="bulk-release-sync-scope">{t("bulkReleaseSync.scopeLabel")}</Label>
            <Select
              id="bulk-release-sync-scope"
              value={scope}
              disabled={pending}
              onChange={(event) => {
                setScope(event.target.value as "current" | "all");
                requestId.current = null;
              }}
            >
              <option value="current">
                {t("bulkReleaseSync.scopeCurrent", { framework: t(`frameworks.${framework}`) })}
              </option>
              <option value="all">{t("bulkReleaseSync.scopeAll")}</option>
            </Select>
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={pending}
              onClick={() => setOpen(false)}
            >
              {t("bulkReleaseSync.close")}
            </Button>
            <Button
              type="button"
              data-testid="market-sync-releases-confirm"
              disabled={pending}
              onClick={() => void submit()}
            >
              {pending ? <LoaderCircle className="animate-spin" /> : <PackageCheck />}
              {pending ? t("bulkReleaseSync.submitting") : t("bulkReleaseSync.start")}
            </Button>
          </div>
        </div>
      </Dialog>
    </>
  );
}
