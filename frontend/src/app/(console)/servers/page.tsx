import { Suspense } from "react";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Plus } from "lucide-react";
import { PageHeader } from "@/shared/ui/page-header";
import { LinkButton } from "@/shared/ui/link-button";
import { ServerList, ServerListSkeleton } from "@/modules/servers/server-list";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("servers");
  return { title: t("title") };
}

export default async function ServersPage() {
  const t = await getTranslations("servers");
  return (
    <>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <LinkButton href="/servers/new">
            <Plus className="size-4" />
            {t("add")}
          </LinkButton>
        }
      />
      <Suspense fallback={<ServerListSkeleton />}>
        <ServerList />
      </Suspense>
    </>
  );
}
