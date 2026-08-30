import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { getSession } from "@/modules/auth/session";
import { TutorialGuide } from "@/modules/tutorial/tutorial-guide";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("tutorial");
  return { title: t("title") };
}

export default async function DeploymentTutorialPage() {
  const session = await getSession();
  return <TutorialGuide signedIn={session !== null} />;
}
