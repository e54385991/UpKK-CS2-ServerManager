import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { GoogleCallbackClient } from "@/modules/auth/google-callback";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("login");
  return { title: t("googleCallbackTitle") };
}

export default function GoogleCallbackPage() {
  return <GoogleCallbackClient />;
}
