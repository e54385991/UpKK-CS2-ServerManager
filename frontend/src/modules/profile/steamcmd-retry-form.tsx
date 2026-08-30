"use client";

import { useState, type FormEvent } from "react";
import { useTranslations } from "next-intl";
import { saveSteamcmdRetryAction } from "@/modules/profile/actions";
import type { ProfileSettings } from "@/modules/profile/types";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Input, Label } from "@/shared/ui/input";

export function SteamcmdRetryForm({ initial }: { initial: ProfileSettings }) {
  const t = useTranslations("profile");
  const [retries, setRetries] = useState(String(initial.steamcmdMaxRetries));
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);

  async function onSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setBanner(null);
    const parsed = Number(retries);
    const result = await saveSteamcmdRetryAction(parsed);
    setSaving(false);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setRetries(String(result.data.steamcmdMaxRetries));
    setBanner(t("saved"));
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <div className="space-y-1">
          <CardTitle>{t("steamcmdTitle")}</CardTitle>
          <CardDescription>{t("steamcmdHelp")}</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSave} className="space-y-4">
          <div>
            <Label htmlFor="steamcmd-max-retries">{t("steamcmdRetries")}</Label>
            <Input
              id="steamcmd-max-retries"
              type="number"
              min={0}
              max={initial.steamcmdMaxRetriesLimit}
              value={retries}
              onChange={(event) => setRetries(event.target.value)}
            />
            <p className="mt-1.5 text-xs text-fg-subtle">
              {t("steamcmdRetriesHelp", {
                default: initial.steamcmdMaxRetriesDefault,
                limit: initial.steamcmdMaxRetriesLimit,
              })}
            </p>
          </div>
          {banner ? (
            <p className="text-sm text-fg-muted" role="status">
              {banner}
            </p>
          ) : null}
          <Button type="submit" disabled={saving}>
            {saving ? t("saving") : t("save")}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
