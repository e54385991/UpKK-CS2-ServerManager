import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { getSession } from "@/modules/auth/session";
import { PublicAuthFrame } from "@/modules/auth/public-frame";
import { RegisterForm } from "@/modules/auth/register-form";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("register");
  return { title: t("title") };
}

export default async function RegisterPage() {
  if (await getSession()) redirect("/overview");

  return (
    <PublicAuthFrame>
      <RegisterForm />
    </PublicAuthFrame>
  );
}