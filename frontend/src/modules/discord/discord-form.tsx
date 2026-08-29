"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { removeDiscordAction, saveDiscordAction, testDiscordAction } from "@/modules/discord/actions";
import type { DiscordBot } from "@/modules/discord/types";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Input, Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";
import { Switch } from "@/shared/ui/switch";

export function DiscordForm({ initial }: { initial: DiscordBot }) {
  const t = useTranslations("discord");
  const [bot, setBot] = useState(initial);
  const [token, setToken] = useState("");
  const [enabled, setEnabled] = useState(initial.enabled);
  const [mode, setMode] = useState(initial.messageTriggerMode);
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  async function save() {
    setPending("save");
    setBanner(null);
    const result = await saveDiscordAction({
      token: token.trim() || undefined,
      enabled,
      messageTriggerMode: mode,
    });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setBot(result.data);
    setToken("");
    setBanner(t("saved"));
  }

  async function test() {
    setPending("test");
    setBanner(null);
    const result = await testDiscordAction(token.trim() || undefined);
    setPending(null);
    setBanner(result.ok ? result.data.message : result.error || t("failed"));
  }

  async function remove() {
    if (!window.confirm(t("removeConfirm"))) return;
    setPending("remove");
    const result = await removeDiscordAction();
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setBot({
      ...bot,
      enabled: false,
      tokenConfigured: false,
      username: null,
      connectionStatus: "not_configured",
      lastError: null,
      inviteUrl: null,
    });
    setEnabled(false);
    setBanner(result.data.message);
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("help")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {banner ? <p className="text-sm text-fg-muted">{banner}</p> : null}
        <div className="flex flex-wrap gap-2">
          <Badge tone={bot.tokenConfigured ? "ok" : "neutral"}>
            {bot.tokenConfigured ? t("tokenOn") : t("tokenOff")}
          </Badge>
          <Badge>{bot.connectionStatus}</Badge>
          {bot.username ? <Badge>{bot.username}</Badge> : null}
        </div>
        {bot.lastError ? <p className="text-sm text-danger">{bot.lastError}</p> : null}
        <div className="flex items-center justify-between gap-3">
          <Label htmlFor="discord-enabled">{t("enabled")}</Label>
          <Switch
            id="discord-enabled"
            label={t("enabled")}
            checked={enabled}
            onCheckedChange={setEnabled}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="discord-token">{t("token")}</Label>
          <Input
            id="discord-token"
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder={bot.tokenConfigured ? t("tokenKeep") : t("tokenPlaceholder")}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="discord-mode">{t("trigger")}</Label>
          <Select
            id="discord-mode"
            value={mode}
            onChange={(event) =>
              setMode(event.target.value as "mention_only" | "mention_and_greetings")
            }
          >
            <option value="mention_only">{t("mentionOnly")}</option>
            <option value="mention_and_greetings">{t("mentionAndGreetings")}</option>
          </Select>
        </div>
        {bot.inviteUrl ? (
          <a href={bot.inviteUrl} className="text-sm text-primary underline" target="_blank" rel="noreferrer">
            {t("invite")}
          </a>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button type="button" disabled={Boolean(pending)} onClick={() => void save()}>
            {pending === "save" ? t("saving") : t("save")}
          </Button>
          <Button type="button" variant="outline" disabled={Boolean(pending)} onClick={() => void test()}>
            {pending === "test" ? t("testing") : t("test")}
          </Button>
          <Button type="button" variant="outline" disabled={Boolean(pending)} onClick={() => void remove()}>
            {t("remove")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
