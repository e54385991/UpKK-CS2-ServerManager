import { getTranslations } from "next-intl/server";
import { TriangleAlert } from "lucide-react";
import { getPasskeys, getProfile, getProfileAi, getS3Settings } from "@/modules/profile/api";
import { ProfileWorkspace } from "@/modules/profile/profile-workspace";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function ProfilePanel() {
  const t = await getTranslations("profile");
  const tShell = await getTranslations("shell");
  const [profileResult, s3Result, aiResult, passkeysResult] = await Promise.all([
    getProfile(),
    getS3Settings(),
    getProfileAi(),
    getPasskeys(),
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
    <ProfileWorkspace
      profile={profile}
      joined={joined}
      roleLabel={profile.isAdmin ? tShell("admin") : tShell("user")}
      s3={s3Result.ok ? s3Result.data : null}
      ai={aiResult.ok ? aiResult.data : null}
      passkeys={passkeysResult.ok ? passkeysResult.data : []}
      passkeysError={!passkeysResult.ok}
    />
  );
}

export function ProfilePanelSkeleton() {
  return (
    <div className="grid gap-6 lg:grid-cols-[14rem_minmax(0,1fr)]">
      <div className="h-fit rounded-lg border border-line bg-surface p-2 shadow-panel">
        <div className="space-y-1">
          {Array.from({ length: 6 }, (_, index) => (
            <Skeleton key={index} className="h-9 w-full rounded-md" />
          ))}
        </div>
      </div>
      <div className="max-w-2xl space-y-4 rounded-lg border border-line bg-surface p-5 shadow-panel">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-3 w-72" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-9 w-24" />
      </div>
    </div>
  );
}
