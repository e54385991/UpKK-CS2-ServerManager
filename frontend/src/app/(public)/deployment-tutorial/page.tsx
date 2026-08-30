import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { TutorialGuide } from "@/modules/tutorial/tutorial-guide";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("tutorial");
  return { title: t("title") };
}

export default async function DeploymentTutorialPage() {
  return <TutorialGuide />;
}
