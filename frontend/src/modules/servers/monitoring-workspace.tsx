import { Suspense } from "react";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { ServerA2SLogsPanel } from "@/modules/servers/a2s-logs-panel";
import { ServerA2SPanel } from "@/modules/servers/a2s-panel";
import { getServerA2SQuery, listMonitoringLogs } from "@/modules/servers/api";
import { getDiagnosticRecommendation } from "@/modules/servers/diagnostics-api";
import { PluginDiagnosticsPanel } from "@/modules/servers/diagnostics-panel";
import { ServerMonitoringForm } from "@/modules/servers/monitoring-form";
import { getServer } from "@/modules/servers/render-queries";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export function MonitoringWorkspace({ serverId }: { serverId: number }) {
  return (
    <div className="space-y-6">
      <Suspense fallback={<PluginDiagnosticsSkeleton />}>
        <PluginDiagnosticsSection serverId={serverId} />
      </Suspense>
      <Suspense fallback={<ServerA2SSkeleton />}>
        <ServerA2SSection serverId={serverId} />
      </Suspense>
      <Suspense fallback={<ServerA2SLogsSkeleton />}>
        <ServerA2SLogsSection serverId={serverId} />
      </Suspense>
      <Suspense fallback={<ServerMonitoringFormSkeleton />}>
        <ServerMonitoringFormSection serverId={serverId} />
      </Suspense>
    </div>
  );
}

export function MonitoringWorkspaceSkeleton() {
  return (
    <div className="space-y-6">
      <PluginDiagnosticsSkeleton />
      <ServerA2SSkeleton />
      <ServerA2SLogsSkeleton />
      <ServerMonitoringFormSkeleton />
    </div>
  );
}

async function PluginDiagnosticsSection({ serverId }: { serverId: number }) {
  const recommendation = await getDiagnosticRecommendation(serverId);
  return (
    <PluginDiagnosticsPanel
      serverId={serverId}
      recommendation={recommendation.ok ? recommendation.data : null}
    />
  );
}

async function ServerA2SSection({ serverId }: { serverId: number }) {
  const [t, result, query] = await Promise.all([
    getTranslations("serverDetail"),
    getServer(serverId),
    getServerA2SQuery(serverId),
  ]);
  if (!result.ok && result.status === 404) notFound();
  if (!result.ok) {
    return (
      <FetchError
        message={t("fetchError", { status: result.status || "network" })}
      />
    );
  }
  return (
    <ServerA2SPanel
      server={result.data}
      initialQuery={query.ok ? query.data : null}
    />
  );
}

async function ServerA2SLogsSection({ serverId }: { serverId: number }) {
  const logs = await listMonitoringLogs(serverId, "a2s_check");
  return (
    <ServerA2SLogsPanel
      serverId={serverId}
      initialLogs={logs.ok ? logs.data : []}
    />
  );
}

async function ServerMonitoringFormSection({ serverId }: { serverId: number }) {
  const [t, result] = await Promise.all([
    getTranslations("serverDetail"),
    getServer(serverId),
  ]);
  if (!result.ok && result.status === 404) notFound();
  if (!result.ok) {
    return (
      <FetchError
        message={t("fetchError", { status: result.status || "network" })}
      />
    );
  }
  return <ServerMonitoringForm server={result.data} />;
}

function FetchError({ message }: { message: string }) {
  return (
    <Card className="border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
      {message}
    </Card>
  );
}

export function PluginDiagnosticsSkeleton() {
  return (
    <div className="space-y-4" data-testid="plugin-diagnostics-loading">
      <Skeleton className="h-16 rounded-lg" />
      <Skeleton className="h-40 rounded-lg" />
    </div>
  );
}

export function ServerA2SSkeleton() {
  return (
    <div data-testid="a2s-panel-loading">
      <Skeleton className="h-80 rounded-lg" />
    </div>
  );
}

export function ServerA2SLogsSkeleton() {
  return (
    <div data-testid="a2s-logs-loading">
      <Skeleton className="h-40 rounded-lg" />
    </div>
  );
}

export function ServerMonitoringFormSkeleton() {
  return (
    <div className="max-w-2xl" data-testid="monitoring-form-loading">
      <Skeleton className="h-64 rounded-lg" />
    </div>
  );
}
