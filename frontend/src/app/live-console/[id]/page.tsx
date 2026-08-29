import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { ConsolePanel, ConsolePanelSkeleton } from "@/modules/console/console-panel";
import {
  parseLiveConsoleView,
  resolveLiveConsoleView,
} from "@/modules/console/live-console";
import {
  getCurrentServerOperation,
  getDeploymentLock,
  getServer,
} from "@/modules/servers/api";
import {
  LiveDeployPanel,
  LiveDeployPanelSkeleton,
} from "@/modules/servers/live-deploy-panel";
import { parseServerId } from "@/modules/servers/workspace";

export async function generateMetadata({
  searchParams,
}: {
  searchParams: Promise<{ view?: string }>;
}): Promise<Metadata> {
  const [{ view }, t] = await Promise.all([
    searchParams,
    getTranslations("console"),
  ]);
  return {
    title:
      parseLiveConsoleView(view) === "deploy"
        ? t("liveDeployTitle")
        : t("liveTitle"),
  };
}

export default async function LiveConsolePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ view?: string }>;
}) {
  const [{ id }, { view }] = await Promise.all([params, searchParams]);
  const serverId = parseServerId(id);
  if (serverId == null) notFound();

  const preferredView = parseLiveConsoleView(view);
  const [server, current, lock] = await Promise.all([
    getServer(serverId),
    getCurrentServerOperation(serverId),
    getDeploymentLock(serverId),
  ]);
  if (!server.ok && server.status === 404) notFound();

  const mode = resolveLiveConsoleView({
    preferredView,
    operation: current.ok ? current.data : null,
    serverStatus: server.ok ? server.data.status : null,
    lockActive: lock.ok && lock.data.lockActive,
  });

  if (mode === "deploy") {
    return (
      <Suspense fallback={<LiveDeployPanelSkeleton />}>
        <LiveDeployPanel
          serverId={serverId}
          serverStatus={server.ok ? server.data.status : "unknown"}
          aptMirror={server.ok ? server.data.aptMirror : null}
        />
      </Suspense>
    );
  }

  return (
    <Suspense fallback={<ConsolePanelSkeleton />}>
      <ConsolePanel serverId={serverId} />
    </Suspense>
  );
}
