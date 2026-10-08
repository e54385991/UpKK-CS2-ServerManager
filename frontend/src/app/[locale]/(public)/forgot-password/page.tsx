import { Suspense } from "react";
import type { Metadata } from "next";
import { redirect } from "@/i18n/redirect";
import { getTranslations } from "next-intl/server";
import { ClientMessages } from "@/i18n/client-messages";
import { FORGOT_PASSWORD_CLIENT_NAMESPACES } from "@/i18n/namespaces";
import { getSession } from "@/modules/auth/render-session";
import { ForgotPasswordForm } from "@/modules/auth/forgot-form";
import { PublicAuthFrame } from "@/modules/auth/public-frame";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("forgotPassword");
  return { title: t("title") };
}

export default function ForgotPasswordPage() {

  return (
    <ClientMessages namespaces={FORGOT_PASSWORD_CLIENT_NAMESPACES}>
      <Suspense fallback={null}><PublicSessionGate /></Suspense>
      <PublicAuthFrame>
        <ForgotPasswordForm />
      </PublicAuthFrame>
    </ClientMessages>
  );
}

async function PublicSessionGate() {
  if (await getSession()) redirect("/overview");
  return null;
}
