import { Suspense } from "react";
import type { Metadata } from "next";
import { redirect } from "@/i18n/redirect";
import { getTranslations } from "next-intl/server";
import { ClientMessages } from "@/i18n/client-messages";
import { LOGIN_CLIENT_NAMESPACES } from "@/i18n/namespaces";
import { getSession } from "@/modules/auth/render-session";
import { getRegistrationConfig } from "@/modules/auth/api";
import { LoginForm } from "@/modules/auth/login-form";
import { PublicAuthFrame } from "@/modules/auth/public-frame";
import { Skeleton } from "@/shared/ui/skeleton";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("login");
  return { title: t("submit") };
}

export default function LoginPage() {
  return <ClientMessages namespaces={LOGIN_CLIENT_NAMESPACES}>
    <Suspense fallback={null}><PublicSessionGate /></Suspense>
    <PublicAuthFrame>
      <Suspense fallback={<LoginFormSkeleton />}><LoginFields /></Suspense>
    </PublicAuthFrame>
  </ClientMessages>;
}
async function LoginFields() {
  const registration = await getRegistrationConfig();
  return <LoginForm registrationEnabled={registration.ok ? registration.data.registration_enabled : true} />;
}
async function PublicSessionGate() {
  if (await getSession()) redirect("/overview");
  return null;
}

function LoginFormSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-11 w-full" />
    </div>
  );
}
