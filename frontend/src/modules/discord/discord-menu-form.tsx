"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import {
  pushDiscordMenuAction,
  refreshDiscordMenuOptionsAction,
} from "@/modules/discord/actions";
import type { DiscordOptions } from "@/modules/discord/types";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";

export function DiscordMenuForm({ options: initial }: { options: DiscordOptions }) {
  const t = useTranslations("discord");
  const [options, setOptions] = useState(initial);
  const [guildId, setGuildId] = useState(initial.guilds[0]?.id ?? "");
  const [channelId, setChannelId] = useState(initial.channels[0]?.id ?? "");
  const [pending, setPending] = useState(false);
  const [banner, setBanner] = useState<string | null>(initial.message);

  async function onGuild(next: string) {
    setGuildId(next);
    const result = await refreshDiscordMenuOptionsAction(next || undefined);
    if (result.ok) {
      setOptions(result.data);
      setChannelId(result.data.channels[0]?.id ?? "");
      setBanner(result.data.message);
    }
  }

  async function push() {
    if (!guildId || !channelId) return;
    setPending(true);
    setBanner(null);
    const result = await pushDiscordMenuAction(guildId, channelId);
    setPending(false);
    setBanner(result.ok ? t("pushed") : result.error || t("failed"));
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <CardTitle>{t("menuTitle")}</CardTitle>
        <CardDescription>{t("menuHelp")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {banner ? <p className="text-sm text-fg-muted">{banner}</p> : null}
        {!options.tokenConfigured ? (
          <p className="text-sm text-warn">{t("tokenMissing")}</p>
        ) : null}
        <div className="space-y-2">
          <Label htmlFor="menu-guild">{t("guild")}</Label>
          <Select
            id="menu-guild"
            value={guildId}
            onChange={(event) => void onGuild(event.target.value)}
            disabled={!options.tokenConfigured}
          >
            <option value="">{t("noGuilds")}</option>
            {options.guilds.map((guild) => (
              <option key={guild.id} value={guild.id}>
                {guild.name}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="menu-channel">{t("channel")}</Label>
          <Select
            id="menu-channel"
            value={channelId}
            onChange={(event) => setChannelId(event.target.value)}
            disabled={!options.tokenConfigured}
          >
            <option value="">{t("noGuilds")}</option>
            {options.channels.map((channel) => (
              <option key={channel.id} value={channel.id}>
                {channel.name}
              </option>
            ))}
          </Select>
        </div>
        <Button
          type="button"
          disabled={pending || !guildId || !channelId}
          onClick={() => void push()}
        >
          {pending ? t("pushing") : t("pushMenu")}
        </Button>
      </CardContent>
    </Card>
  );
}
