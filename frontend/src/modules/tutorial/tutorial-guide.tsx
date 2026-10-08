import Image from "next/image";
import { getTranslations } from "next-intl/server";
import { LinkButton } from "@/shared/ui/link-button";
import { PageHeader } from "@/shared/ui/page-header";
import { Card, CardContent } from "@/shared/ui/card";
import { Suspense } from "react";
import { getSession } from "@/modules/auth/render-session";

const TUTORIAL_IMAGE_VERSION = "20260831b";
const TUTORIAL_IMAGE_BASE = "/tutorial/deploy";

const TUTORIAL_STEPS = [
  { step: 1, title: "step1Title", body: "step1Body" },
  { step: 2, title: "step2Title", body: "step2Body" },
  { step: 3, title: "step3Title", body: "step3Body" },
  { step: 4, title: "step4Title", body: "step4Body" },
  { step: 5, title: "step5Title", body: "step5Body" },
  { step: 6, title: "step6Title", body: "step6Body" },
  { step: 8, title: "step8Title", body: "step8Body" },
  { step: 9, title: "step9Title", body: "step9Body" },
  { step: 10, title: "step10Title", body: "step10Body" },
] as const;

export async function TutorialGuide() {
  const t = await getTranslations("tutorial");

  return (
    <div
      className="mx-auto max-w-3xl"
      data-testid="tutorial-guide"
    >
      <PageHeader
        title={t("title")}
        description={t("subtitle")}
        actions={
          <Suspense fallback={<LinkButton href="/login" variant="outline">{t("backToLogin")}</LinkButton>}>
            <TutorialActions header />
          </Suspense>
        }
      />

      <Card className="mb-6 border-warn/30 bg-warn-muted/30 px-5 py-4 text-sm text-warn">
        {t("chinaSteamWarning")}
      </Card>

      <ol className="space-y-8">
        {TUTORIAL_STEPS.map(({ step, title, body }) => (
          <li key={step} id={`step-${step}`} data-testid={`tutorial-step-${step}`}>
            <h2 className="mb-1 text-sm font-semibold text-fg">
              {t("stepLabel", { step })} · {t(title)}
            </h2>
            <p className="mb-3 text-sm text-fg-muted">{t(body)}</p>
            <Card>
              <CardContent className="p-3 sm:p-4">
                <Image
                  src={`${TUTORIAL_IMAGE_BASE}/${step}.webp?v=${TUTORIAL_IMAGE_VERSION}`}
                  alt={t("stepAlt", { step })}
                  width={1280}
                  height={720}
                  loading={step === 1 ? "eager" : "lazy"}
                  unoptimized
                  className="h-auto w-full rounded-md border border-line"
                />
              </CardContent>
            </Card>
          </li>
        ))}
      </ol>

      <div className="mt-8 flex flex-wrap gap-3">
        <Suspense fallback={<LinkButton href="/login" variant="outline">{t("backToLogin")}</LinkButton>}>
          <TutorialActions />
        </Suspense>
        <LinkButton href="/servers">{t("manageServers")}</LinkButton>
      </div>
    </div>
  );
}

async function TutorialActions({ header = false }: { header?: boolean }) {
  const [session, t] = await Promise.all([getSession(), getTranslations("tutorial")]);
  if (!session) return <LinkButton href="/login" variant="outline">{t("backToLogin")}</LinkButton>;
  return header ? <LinkButton href="/servers/new" variant="outline">{t("addServer")}</LinkButton>
    : <LinkButton href="/overview" variant="outline">{t("backToOverview")}</LinkButton>;
}
