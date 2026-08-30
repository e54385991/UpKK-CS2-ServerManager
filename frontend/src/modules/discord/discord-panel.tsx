import { getTranslations } from "next-intl/server";
import { TriangleAlert } from "lucide-react";
import {
  getDiscordBot,
  getDiscordGlobalBinding,
  getDiscordGlobalOptions,
  getDiscordMenuOptions,
} from "@/modules/discord/api";
import { DiscordBindingForm } from "@/modules/discord/discord-binding-form";
import { DiscordForm } from "@/modules/discord/discord-form";
import { DiscordMenuForm } from "@/modules/discord/discord-menu-form";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function DiscordPanel() {
  const t = await getTranslations("discord");
  const [bot, binding, options, menu] = await Promise.all([
    getDiscordBot(),
    getDiscordGlobalBinding(),
    getDiscordGlobalOptions(),
    getDiscordMenuOptions(),
  ]);
  if (!bot.ok) {
    return (
      <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>{t("fetchError", { status: bot.status || "network" })}</span>
      </Card>
    );
  }
  const menuOptions = menu.ok
    ? {
        ...menu.data,
        tokenConfigured:
          menu.data.tokenConfigured || (options.ok && options.data.tokenConfigured),
        guilds:
          menu.data.guilds.length > 0
            ? menu.data.guilds
            : options.ok
              ? options.data.guilds
              : [],
      }
    : options.ok
      ? {
          ...options.data,
          message: t("fetchError", { status: menu.status || "network" }),
        }
      : null;

  return (
    <div className="space-y-6">
      <DiscordForm initial={bot.data} />
      {binding.ok && options.ok ? (
        <DiscordBindingForm scope="global" initial={binding.data} options={options.data} />
      ) : null}
      {menuOptions ? <DiscordMenuForm options={menuOptions} /> : null}
    </div>
  );
}

export function DiscordPanelSkeleton() {
  return (
    <div className="max-w-2xl rounded-lg border border-line bg-surface p-5 shadow-panel">
      <Skeleton className="mb-4 h-4 w-40" />
      <Skeleton className="h-10 w-full" />
    </div>
  );
}
