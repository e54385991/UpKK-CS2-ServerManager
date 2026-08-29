import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { getDiagnosticRecommendation } from "@/modules/servers/diagnostics-api";
import { PluginDiagnosticsPanel } from "@/modules/servers/diagnostics-panel";
import { getServer } from "@/modules/servers/api";
import { ServerMonitoringForm } from "@/modules/servers/monitoring-form";
import { parseServerId } from "@/modules/servers/workspace";
import { Card } from "@/shared/ui/card";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverWorkspace");
  return { title: t("categories.monitoring") };
}

export default async function ServerMonitoringPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();

  const [t, result, recommendation] = await Promise.all([
    getTranslations("serverDetail"),
    getServer(serverId),
    getDiagnosticRecommendation(serverId),
  ]);
  if (!result.ok && result.status === 404) notFound();
  if (!result.ok) {
    return (
      <Card className="border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        {t("fetchError", { status: result.status || "network" })}
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <PluginDiagnosticsPanel
        serverId={serverId}
        recommendation={recommendation.ok ? recommendation.data : null}
      />
      <ServerMonitoringForm server={result.data} />
    </div>
  );
}
