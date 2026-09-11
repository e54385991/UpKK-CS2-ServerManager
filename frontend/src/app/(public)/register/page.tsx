import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { getTranslations } from "next-intl/server";
import type { Route } from "next";
import { getRegistrationConfig } from "@/modules/auth/api";
import { getSession } from "@/modules/auth/render-session";
import { PublicAuthFrame } from "@/modules/auth/public-frame";
import { RegisterForm } from "@/modules/auth/register-form";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("register");
  return { title: t("title") };
}

export default async function RegisterPage() {
  const [session, t, registration] = await Promise.all([
    getSession(),
    getTranslations("register"),
    getRegistrationConfig(),
  ]);
  if (session) redirect("/overview");

  if (registration.ok && !registration.data.registration_enabled) {
    return (
      <PublicAuthFrame>
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
      </PublicAuthFrame>
    );
  }

  return (
    <PublicAuthFrame>
      <RegisterForm />
    </PublicAuthFrame>
  );
}
