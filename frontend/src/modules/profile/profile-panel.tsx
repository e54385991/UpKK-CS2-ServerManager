import { getTranslations } from "next-intl/server";
import { TriangleAlert } from "lucide-react";
import { getProfile, getProfileAi, getS3Settings } from "@/modules/profile/api";
import { ApiKeyForm } from "@/modules/profile/api-key-form";
import { PasswordForm } from "@/modules/profile/password-form";
import { ProfileCredentialsForm } from "@/modules/profile/profile-credentials-form";
import { S3Form } from "@/modules/profile/s3-form";
import { SteamcmdRetryForm } from "@/modules/profile/steamcmd-retry-form";
import { UserAiForm } from "@/modules/profile/user-ai-form";
import { Badge } from "@/shared/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function ProfilePanel() {
  const t = await getTranslations("profile");
  const tShell = await getTranslations("shell");
  const [profileResult, s3Result, aiResult] = await Promise.all([
    getProfile(),
    getS3Settings(),
    getProfileAi(),
  ]);

  if (!profileResult.ok) {
    return (
      <Card className="flex max-w-2xl items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>{t("fetchError", { status: profileResult.status || "network" })}</span>
      </Card>
    );
  }

  const profile = profileResult.data;
  const joined = profile.createdAt
    ? profile.createdAt.slice(0, 19).replace("T", " ")
    : t("joinedUnknown");

  return (
    <div className="space-y-6">
      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>{t("account")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Row label={t("username")} value={profile.username} />
          <Row label={t("email")} value={profile.email ?? "—"} />
          <div className="flex items-center justify-between">
            <span className="text-sm text-fg-muted">{t("role")}</span>
            <Badge tone={profile.isAdmin ? "primary" : "neutral"}>
              {profile.isAdmin ? tShell("admin") : tShell("user")}
            </Badge>
          </div>
          <Row label={t("joinedAt")} value={joined} />
        </CardContent>
      </Card>
      <ProfileCredentialsForm initial={profile} />
      {s3Result.ok ? <S3Form initial={s3Result.data} /> : null}
      {aiResult.ok ? <UserAiForm initial={aiResult.data} /> : null}
      <SteamcmdRetryForm initial={profile} />
      <PasswordForm />
      <ApiKeyForm initial={profile} />
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-sm text-fg-muted">{label}</span>
      <span className="text-sm font-medium text-fg">{value}</span>
    </div>
  );
}

export function ProfilePanelSkeleton() {
  return (
    <div className="max-w-2xl space-y-6">
      {Array.from({ length: 3 }, (_, index) => (
        <div
          key={index}
          className="rounded-lg border border-line bg-surface p-5 shadow-panel"
        >
          <div className="space-y-2">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-3 w-72" />
          </div>
          <div className="mt-5 space-y-3">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-9 w-24" />
          </div>
        </div>
      ))}
    </div>
  );
}
