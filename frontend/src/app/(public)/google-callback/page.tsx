import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { ClientMessages } from "@/i18n/client-messages";
import { LOGIN_CLIENT_NAMESPACES } from "@/i18n/namespaces";
import { GoogleCallbackClient } from "@/modules/auth/google-callback";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("login");
  return { title: t("googleCallbackTitle") };
}

export default function GoogleCallbackPage() {
  return (
    <ClientMessages namespaces={LOGIN_CLIENT_NAMESPACES}>
      <GoogleCallbackClient />
    </ClientMessages>
  );
}
