import type { Metadata } from "next";
import { redirect } from "next/navigation";
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

export default async function ForgotPasswordPage() {
  if (await getSession()) redirect("/overview");

  return (
    <ClientMessages namespaces={FORGOT_PASSWORD_CLIENT_NAMESPACES}>
      <PublicAuthFrame>
        <ForgotPasswordForm />
      </PublicAuthFrame>
    </ClientMessages>
  );
}
