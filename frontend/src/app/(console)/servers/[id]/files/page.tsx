import { Suspense } from "react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { FilesPanel, FilesPanelSkeleton } from "@/modules/files/files-panel";
import { parseServerId } from "@/modules/servers/workspace";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverWorkspace");
  return { title: t("categories.files") };
}

export default async function ServerFilesPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ path?: string }>;
}) {
  const [{ id }, sp] = await Promise.all([params, searchParams]);
  const serverId = parseServerId(id);
  if (serverId == null) notFound();

  return (
    <Suspense fallback={<FilesPanelSkeleton />}>
      <FilesPanel serverId={serverId} path={sp.path} />
    </Suspense>
  );
}
