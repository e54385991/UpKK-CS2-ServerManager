import { Suspense } from "react";
import type { Metadata } from "next";
import { redirect } from "next/navigation";
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

export default async function LoginPage() {
  const [session, registration] = await Promise.all([
    getSession(),
    getRegistrationConfig(),
  ]);
  if (session) redirect("/overview");

  return (
    <ClientMessages namespaces={LOGIN_CLIENT_NAMESPACES}>
      <PublicAuthFrame>
        <Suspense fallback={<LoginFormSkeleton />}>
          <LoginForm
            registrationEnabled={registration.ok ? registration.data.registration_enabled : true}
          />
        </Suspense>
      </PublicAuthFrame>
    </ClientMessages>
  );
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
