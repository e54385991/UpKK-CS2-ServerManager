import Image from "next/image";
import { getTranslations } from "next-intl/server";
import { LinkButton } from "@/shared/ui/link-button";
import { PageHeader } from "@/shared/ui/page-header";
import { Card, CardContent } from "@/shared/ui/card";
import { cn } from "@/shared/lib/cn";

const TUTORIAL_IMAGE_VERSION = "20260831";

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

export async function TutorialGuide({ signedIn }: { signedIn: boolean }) {
  const t = await getTranslations("tutorial");

  return (
    <div
      className={cn("mx-auto max-w-3xl", signedIn ? "" : "px-4 py-10")}
      data-testid="tutorial-guide"
    >
      <PageHeader
        title={t("title")}
        description={t("subtitle")}
        actions={
          signedIn ? (
            <LinkButton href="/servers/new" variant="outline">
              {t("addServer")}
            </LinkButton>
          ) : (
            <LinkButton href="/login" variant="outline">
              {t("backToLogin")}
            </LinkButton>
          )
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
                  src={`/static/images/aliyun-deploy/${step}.webp?v=${TUTORIAL_IMAGE_VERSION}`}
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
        {signedIn ? (
          <LinkButton href="/overview" variant="outline">
            {t("backToOverview")}
          </LinkButton>
        ) : (
          <LinkButton href="/login" variant="outline">
            {t("backToLogin")}
          </LinkButton>
        )}
        <LinkButton href="/servers">{t("manageServers")}</LinkButton>
      </div>
    </div>
  );
}
