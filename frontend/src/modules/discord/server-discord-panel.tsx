import { getTranslations } from "next-intl/server";
import { TriangleAlert } from "lucide-react";
import {
  getServerAgentPolicy,
  getServerDiscordBinding,
  getServerDiscordOptions,
} from "@/modules/discord/api";
import { DiscordBindingForm } from "@/modules/discord/discord-binding-form";
import { DiscordPolicyForm } from "@/modules/discord/discord-policy-form";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function ServerDiscordPanel({ serverId }: { serverId: number }) {
  const t = await getTranslations("discord");
  const [binding, options, policy] = await Promise.all([
    getServerDiscordBinding(serverId),
    getServerDiscordOptions(serverId),
    getServerAgentPolicy(serverId),
  ]);
  if (!binding.ok) {
    return (
      <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>{t("fetchError", { status: binding.status || "network" })}</span>
      </Card>
    );
  }
  return (
    <div className="space-y-6">
      <DiscordBindingForm
        scope="server"
        serverId={serverId}
        initial={binding.data}
        options={options.ok ? options.data : {
          tokenConfigured: false,
          guilds: [],
          channels: [],
          roles: [],
          message: options.ok ? null : t("fetchError", { status: options.status || "network" }),
        }}
      />
      {policy.ok ? <DiscordPolicyForm serverId={serverId} initial={policy.data} /> : null}
    </div>
  );
}

export function ServerDiscordPanelSkeleton() {
  return (
    <div className="max-w-2xl space-y-4 rounded-lg border border-line bg-surface p-5 shadow-panel">
      <Skeleton className="h-4 w-40" />
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-10 w-full" />
    </div>
  );
}
