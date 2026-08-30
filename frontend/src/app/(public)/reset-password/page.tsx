import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { ResetPasswordForm } from "@/modules/auth/reset-form";
import { PublicAuthFrame } from "@/modules/auth/public-frame";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("resetPassword");
  return { title: t("title") };
}

export default async function ResetPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string | string[] }>;
}) {
  const params = await searchParams;
  const raw = params.token;
  const token = typeof raw === "string" ? raw : "";

  return (
    <PublicAuthFrame>
      <ResetPasswordForm token={token} />
    </PublicAuthFrame>
  );
}
