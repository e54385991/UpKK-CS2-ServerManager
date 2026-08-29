"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { saveServerAgentPolicyAction } from "@/modules/discord/actions";
import { AGENT_CAPABILITIES, type AgentPolicy } from "@/modules/discord/types";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Label } from "@/shared/ui/input";
import { Switch } from "@/shared/ui/switch";

export function DiscordPolicyForm({
  serverId,
  initial,
}: {
  serverId: number;
  initial: AgentPolicy;
}) {
  const t = useTranslations("discord");
  const [enabled, setEnabled] = useState(initial.enabled);
  const [capabilities, setCapabilities] = useState<string[]>([...initial.capabilities]);
  const [pending, setPending] = useState(false);
  const [banner, setBanner] = useState<string | null>(initial.disabledReason);

  function toggle(name: string) {
    setCapabilities((current) =>
      current.includes(name) ? current.filter((item) => item !== name) : [...current, name],
    );
  }

  async function save() {
    setPending(true);
    setBanner(null);
    const result = await saveServerAgentPolicyAction(serverId, enabled, capabilities);
    setPending(false);
    setBanner(result.ok ? t("saved") : result.error || t("failed"));
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <CardTitle>{t("policyTitle")}</CardTitle>
        <CardDescription>{t("policyHelp")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {banner ? <p className="text-sm text-fg-muted">{banner}</p> : null}
        <div className="flex items-center justify-between gap-3">
          <Label htmlFor="agent-enabled">{t("policyEnabled")}</Label>
          <Switch
            id="agent-enabled"
            label={t("policyEnabled")}
            checked={enabled}
            onCheckedChange={setEnabled}
          />
        </div>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">{t("capabilities")}</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {AGENT_CAPABILITIES.map((name) => (
              <label key={name} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={capabilities.includes(name)}
                  onChange={() => toggle(name)}
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
