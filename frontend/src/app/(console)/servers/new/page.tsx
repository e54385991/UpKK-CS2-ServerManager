import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { PageHeader } from "@/shared/ui/page-header";
import { LinkButton } from "@/shared/ui/link-button";
import { CreateServerForm } from "@/modules/servers/create-form";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverNew");
  return { title: t("title") };
}

export default async function NewServerPage() {
  const [t, tServers] = await Promise.all([
    getTranslations("serverNew"),
    getTranslations("servers"),
  ]);
  return (
    <>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <LinkButton href="/servers" variant="outline">
            {tServers("backToList")}
          </LinkButton>
        }
      />
      <CreateServerForm />
    </>
  );
}
