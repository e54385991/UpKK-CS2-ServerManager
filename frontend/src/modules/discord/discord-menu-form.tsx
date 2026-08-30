"use client";

import { useEffect, useRef, useState } from "react";
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
  const loadedGuild = useRef<string | null>(initial.channels.length > 0 ? guildId : null);

  async function loadOptions(nextGuild?: string) {
    const result = await refreshDiscordMenuOptionsAction(nextGuild || undefined);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setOptions(result.data);
    const nextChannels = result.data.channels;
    setChannelId((current) =>
      nextGuild && nextChannels.some((item) => item.id === current)
        ? current
        : (nextChannels[0]?.id ?? ""),
    );
    setBanner(
      result.data.message ??
        (nextGuild && nextChannels.length === 0 ? t("noChannels") : null),
    );
  }

  async function onGuild(next: string) {
    setGuildId(next);
    loadedGuild.current = next || null;
    if (!next) {
      setChannelId("");
      return;
    }
    await loadOptions(next);
  }

  useEffect(() => {
    const selected = initial.guilds[0]?.id;
    if (!selected || initial.channels.length > 0 || loadedGuild.current === selected) {
      return;
    }
    loadedGuild.current = selected;
    void refreshDiscordMenuOptionsAction(selected).then((result) => {
      if (!result.ok) {
        setBanner(result.error || t("failed"));
        return;
      }
      setOptions(result.data);
      setChannelId(result.data.channels[0]?.id ?? "");
      setBanner(
        result.data.message ??
          (result.data.channels.length === 0 ? t("noChannels") : null),
      );
    });
  }, [initial.channels.length, initial.guilds, t]);

  async function push() {
    if (!guildId || !channelId) return;
    setPending(true);
    setBanner(null);
    const result = await pushDiscordMenuAction(guildId, channelId);
    setPending(false);
    setBanner(result.ok ? t("pushed") : result.error || t("failed"));
  }

  const canSelect = options.tokenConfigured || options.guilds.length > 0;

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
            disabled={!canSelect}
          >
            <option value="">{options.guilds.length ? t("selectGuild") : t("noGuilds")}</option>
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
            disabled={!canSelect || !guildId}
          >
            <option value="">
              {options.channels.length ? t("selectChannel") : t("noChannels")}
            </option>
            {options.channels.map((channel) => (
              <option key={channel.id} value={channel.id}>
                {channel.name}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            disabled={pending || !guildId || !channelId}
            onClick={() => void push()}
          >
            {pending ? t("pushing") : t("pushMenu")}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={pending}
            onClick={() => {
              loadedGuild.current = null;
              void loadOptions(guildId || undefined);
            }}
          >
            {t("refreshOptions")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
