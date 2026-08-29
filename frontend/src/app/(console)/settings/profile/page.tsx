import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { getSession } from "@/modules/auth/session";
import { PageHeader } from "@/shared/ui/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/ui/card";
import { Badge } from "@/shared/ui/badge";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("profile");
  return { title: t("title") };
}

export default async function ProfilePage() {
  const [session, t, tShell] = await Promise.all([
    getSession(),
    getTranslations("profile"),
    getTranslations("shell"),
  ]);

  return (
    <>
      <PageHeader title={t("title")} description={t("description")} />
      <Card className="max-w-xl">
        <CardHeader>
          <CardTitle>{t("account")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field label={t("username")} value={session?.username ?? "—"} />
          <Field label={t("email")} value={session?.email ?? "—"} />
          <div className="flex items-center justify-between">
            <span className="text-sm text-fg-muted">{t("role")}</span>
            <Badge tone={session?.isAdmin ? "primary" : "neutral"}>
              {session?.isAdmin ? tShell("admin") : tShell("user")}
            </Badge>
          </div>
        </CardContent>
      </Card>
    </>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-fg-muted">{label}</span>
      <span className="text-sm font-medium text-fg">{value}</span>
    </div>
  );
}
