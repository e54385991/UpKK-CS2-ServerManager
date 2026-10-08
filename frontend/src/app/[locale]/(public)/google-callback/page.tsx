import { Suspense } from "react";
import { AuthFormSkeleton } from "@/modules/auth/form-skeleton";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { ClientMessages } from "@/i18n/client-messages";
import { LOGIN_CLIENT_NAMESPACES } from "@/i18n/namespaces";
import { GoogleCallbackClient } from "@/modules/auth/google-callback";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("login");
  return { title: t("googleCallbackTitle") };
}

export default async function GoogleCallbackPage() {
  const t = await getTranslations("login");
  return (
    <ClientMessages namespaces={LOGIN_CLIENT_NAMESPACES}>
      <section data-testid="google-callback-shell" className="mx-auto w-full max-w-lg space-y-4 px-4 py-12">
        <h1 className="text-lg font-semibold text-fg">{t("googleCallbackTitle")}</h1>
        <Suspense fallback={<AuthFormSkeleton />}><GoogleCallbackClient /></Suspense>
      </section>
    </ClientMessages>
  );
}
