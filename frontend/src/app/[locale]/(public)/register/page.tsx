import { Suspense } from "react";
import { AuthFormSkeleton } from "@/modules/auth/form-skeleton";
import type { Metadata } from "next";
import { Link } from "@/i18n/navigation";
import { redirect } from "@/i18n/redirect";
import { getTranslations } from "next-intl/server";
import { ClientMessages } from "@/i18n/client-messages";
import { REGISTER_CLIENT_NAMESPACES } from "@/i18n/namespaces";
import type { PublicRoute as Route } from "@/i18n/public-route";
import { getRegistrationConfig } from "@/modules/auth/api";
import { getSession } from "@/modules/auth/render-session";
import { PublicAuthFrame } from "@/modules/auth/public-frame";
import { RegisterForm } from "@/modules/auth/register-form";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("register");
  return { title: t("title") };
}

async function RegistrationFields() {
  const [t, registration] = await Promise.all([
    getTranslations("register"),
    getRegistrationConfig(),
  ]);

  if (registration.ok && !registration.data.registration_enabled) {
    return (
      <>
          <div className="space-y-4">
            <div>
              <h2 className="text-base font-semibold text-fg">{t("disabledTitle")}</h2>
              <p className="mt-1 text-sm text-fg-muted">{t("disabledMessage")}</p>
            </div>
            <Link
              href={"/login" as Route}
              className="block text-center text-sm text-primary hover:underline"
            >
              {t("loginHere")}
            </Link>
          </div>
        </>
    );
  }

  return (
    <RegisterForm />
  );
}

export default function RegisterPage() {
  return <ClientMessages namespaces={REGISTER_CLIENT_NAMESPACES}>
    <Suspense fallback={null}><PublicSessionGate /></Suspense>
    <PublicAuthFrame><Suspense fallback={<AuthFormSkeleton />}><RegistrationFields /></Suspense></PublicAuthFrame>
  </ClientMessages>;
}
async function PublicSessionGate() {
  if (await getSession()) redirect("/overview");
  return null;
}
