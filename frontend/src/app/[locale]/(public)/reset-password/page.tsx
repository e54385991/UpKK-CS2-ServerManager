import { Suspense } from "react";
import { AuthFormSkeleton } from "@/modules/auth/form-skeleton";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { ClientMessages } from "@/i18n/client-messages";
import { RESET_PASSWORD_CLIENT_NAMESPACES } from "@/i18n/namespaces";
import { ResetPasswordForm } from "@/modules/auth/reset-form";
import { PublicAuthFrame } from "@/modules/auth/public-frame";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("resetPassword");
  return { title: t("title") };
}

export default function ResetPasswordPage({ searchParams }: {
  searchParams: Promise<{ token?: string | string[] }>;
}) {
  return <ClientMessages namespaces={RESET_PASSWORD_CLIENT_NAMESPACES}>
    <PublicAuthFrame><Suspense fallback={<AuthFormSkeleton />}>
      <ResetFields searchParams={searchParams} />
    </Suspense></PublicAuthFrame>
  </ClientMessages>;
}
async function ResetFields({ searchParams }: { searchParams: Promise<{ token?: string | string[] }> }) {
  const { token } = await searchParams;
  return <ResetPasswordForm token={typeof token === "string" ? token : ""} />;
}
