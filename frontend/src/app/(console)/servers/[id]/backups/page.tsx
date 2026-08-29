import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { getServer } from "@/modules/servers/api";
import {
  S3BackupsWorkspace,
  S3BackupsWorkspaceSkeleton,
} from "@/modules/servers/s3-backups-workspace";
import { parseServerId } from "@/modules/servers/workspace";
import { Card } from "@/shared/ui/card";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverWorkspace");
  return { title: t("categories.backups") };
}

export default async function ServerBackupsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = parseServerId(id);
  if (serverId == null) notFound();

  const [t, result] = await Promise.all([
    getTranslations("s3Backups"),
    getServer(serverId),
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
    <Suspense fallback={<S3BackupsWorkspaceSkeleton />}>
      <S3BackupsWorkspace
        serverId={result.data.id}
        serverStatus={result.data.status}
      />
    </Suspense>
  );
}
