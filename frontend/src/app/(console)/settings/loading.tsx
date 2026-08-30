import { Skeleton } from "@/shared/ui/skeleton";
import { SettingsPanelSkeleton } from "@/modules/settings/settings-panel";

export default function Loading() {
  return (
    <>
      <div className="mb-6 space-y-2">
        <Skeleton className="h-6 w-28" />
        <Skeleton className="h-4 w-96" />
      </div>
      <SettingsPanelSkeleton />
    </>
  );
}
