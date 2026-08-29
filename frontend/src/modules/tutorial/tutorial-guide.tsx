import Image from "next/image";
import { getTranslations } from "next-intl/server";
import { LinkButton } from "@/shared/ui/link-button";
import { PageHeader } from "@/shared/ui/page-header";
import { Card, CardContent } from "@/shared/ui/card";

const TUTORIAL_STEPS = [1, 2, 3, 4, 5, 6, 8, 9, 10] as const;

export async function TutorialGuide() {
  const t = await getTranslations("tutorial");

  return (
    <main className="mx-auto max-w-3xl px-4 py-10">
      <PageHeader
        title={t("title")}
        description={t("subtitle")}
        actions={
          <LinkButton href="/servers/new" variant="outline">
            {t("addServer")}
          </LinkButton>
        }
      />

      <Card className="mb-6 border-warn/30 bg-warn-muted/30 px-5 py-4 text-sm text-warn">
        {t("chinaSteamWarning")}
      </Card>

      <ol className="space-y-8">
        {TUTORIAL_STEPS.map((step) => (
          <li key={step}>
            <h2 className="mb-3 text-sm font-semibold text-fg">
              {t("stepLabel", { step })}
            </h2>
            <Card>
              <CardContent className="p-3 sm:p-4">
                <Image
                  src={`/static/images/aliyun-deploy/${step}.webp`}
                  alt={t("stepAlt", { step })}
                  width={1280}
                  height={720}
                  unoptimized
                  className="h-auto w-full rounded-md border border-line"
                />
              </CardContent>
            </Card>
          </li>
        ))}
      </ol>

      <div className="mt-8 flex flex-wrap gap-3">
        <LinkButton href="/login" variant="outline">
          {t("backToLogin")}
        </LinkButton>
        <LinkButton href="/servers">{t("manageServers")}</LinkButton>
      </div>
    </main>
  );
}
