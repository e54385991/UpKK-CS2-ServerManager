"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import {
  refreshDiscordGlobalOptionsAction,
  refreshServerDiscordOptionsAction,
  saveDiscordGlobalAction,
  saveServerDiscordAction,
} from "@/modules/discord/actions";
import {
  DISCORD_CAPABILITIES,
  type DiscordBinding,
  type DiscordBindingInput,
  type DiscordOptions,
} from "@/modules/discord/types";
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

function parseIds(value: string): string[] {
  return value
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function DiscordBindingForm({
  scope,
  serverId,
  initial,
  options: initialOptions,
}: {
  scope: "global" | "server";
  serverId?: number;
  initial: DiscordBinding;
  options: DiscordOptions;
}) {
  const t = useTranslations("discord");
  const [binding, setBinding] = useState(initial);
  const [options, setOptions] = useState(initialOptions);
  const [enabled, setEnabled] = useState(initial.enabled);
  const [guildId, setGuildId] = useState(initial.guildId ?? "");
  const [channelIds, setChannelIds] = useState(initial.channelIds.join(", "));
  const [roleIds, setRoleIds] = useState(initial.roleIds.join(", "));
  const [userIds, setUserIds] = useState(initial.userIds.join(", "));
  const [allowManagers, setAllowManagers] = useState(initial.allowChannelManagers);
  const [allowAdmins, setAllowAdmins] = useState(initial.allowServerAdministrators);
  const [capabilities, setCapabilities] = useState<string[]>([...initial.capabilities]);
  const [syncExisting, setSyncExisting] = useState(false);
  const [pending, setPending] = useState(false);
  const [banner, setBanner] = useState<string | null>(initialOptions.message);

  async function loadOptions(nextGuild?: string) {
    const result =
      scope === "server" && serverId != null
        ? await refreshServerDiscordOptionsAction(serverId, nextGuild || undefined)
        : await refreshDiscordGlobalOptionsAction(nextGuild || undefined);
    if (result.ok) {
      setOptions(result.data);
      if (result.data.message) setBanner(result.data.message);
    }
  }

  function toggleCapability(name: string) {
    setCapabilities((current) =>
      current.includes(name) ? current.filter((item) => item !== name) : [...current, name],
    );
  }

  async function save() {
    setPending(true);
    setBanner(null);
    const input: DiscordBindingInput = {
      enabled,
      guildId: guildId || null,
      channelIds: parseIds(channelIds),
      roleIds: parseIds(roleIds),
      userIds: parseIds(userIds),
      allowChannelManagers: allowManagers,
      allowServerAdministrators: allowAdmins,
      capabilities,
      syncExistingServers: scope === "global" ? syncExisting : false,
    };
    const result =
      scope === "server" && serverId != null
        ? await saveServerDiscordAction(serverId, input)
        : await saveDiscordGlobalAction(input);
    setPending(false);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setBinding(result.data);
    setBanner(t("saved"));
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <CardTitle>{scope === "global" ? t("globalTitle") : t("bindingTitle")}</CardTitle>
        <CardDescription>
          {scope === "global" ? t("globalHelp") : t("bindingHelp")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {banner ? <p className="text-sm text-fg-muted">{banner}</p> : null}
        {!options.tokenConfigured ? (
          <p className="text-sm text-warn">{t("tokenMissing")}</p>
        ) : null}
        {binding.disabledReason ? (
          <p className="text-sm text-fg-muted">{binding.disabledReason}</p>
        ) : null}
        <div className="flex items-center justify-between gap-3">
          <Label htmlFor={`${scope}-discord-enabled`}>{t("enabled")}</Label>
          <Switch
            id={`${scope}-discord-enabled`}
            label={t("enabled")}
            checked={enabled}
            onCheckedChange={setEnabled}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor={`${scope}-guild`}>{t("guild")}</Label>
          {options.guilds.length > 0 ? (
            <Select
              id={`${scope}-guild`}
              value={guildId}
              onChange={(event) => {
                const next = event.target.value;
                setGuildId(next);
                void loadOptions(next);
              }}
            >
              <option value="">{t("noGuilds")}</option>
              {options.guilds.map((guild) => (
                <option key={guild.id} value={guild.id}>
                  {guild.name}
                </option>
              ))}
            </Select>
          ) : (
            <Input
              id={`${scope}-guild`}
              value={guildId}
              onChange={(event) => setGuildId(event.target.value)}
            />
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor={`${scope}-channels`}>{t("channels")}</Label>
          <Input
            id={`${scope}-channels`}
            value={channelIds}
            onChange={(event) => setChannelIds(event.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor={`${scope}-roles`}>{t("roles")}</Label>
          <Input
            id={`${scope}-roles`}
            value={roleIds}
            onChange={(event) => setRoleIds(event.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor={`${scope}-users`}>{t("users")}</Label>
          <Input
            id={`${scope}-users`}
            value={userIds}
            onChange={(event) => setUserIds(event.target.value)}
          />
        </div>
        <div className="flex items-center justify-between gap-3">
          <Label htmlFor={`${scope}-managers`}>{t("allowManagers")}</Label>
          <Switch
            id={`${scope}-managers`}
            label={t("allowManagers")}
            checked={allowManagers}
            onCheckedChange={setAllowManagers}
          />
        </div>
        <div className="flex items-center justify-between gap-3">
          <Label htmlFor={`${scope}-admins`}>{t("allowAdmins")}</Label>
          <Switch
            id={`${scope}-admins`}
            label={t("allowAdmins")}
            checked={allowAdmins}
            onCheckedChange={setAllowAdmins}
          />
        </div>
        {scope === "global" ? (
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="sync-existing">{t("syncExisting")}</Label>
            <Switch
              id="sync-existing"
              label={t("syncExisting")}
              checked={syncExisting}
              onCheckedChange={setSyncExisting}
            />
          </div>
        ) : null}
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">{t("capabilities")}</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {DISCORD_CAPABILITIES.map((name) => (
              <label key={name} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={capabilities.includes(name)}
                  onChange={() => toggleCapability(name)}
                />
                {name}
              </label>
            ))}
          </div>
        </fieldset>
        <Button type="button" disabled={pending} onClick={() => void save()}>
          {pending ? t("saving") : t("save")}
        </Button>
      </CardContent>
    </Card>
  );
}
