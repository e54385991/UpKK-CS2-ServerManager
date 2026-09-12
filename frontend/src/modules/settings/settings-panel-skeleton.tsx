import { Skeleton } from "@/shared/ui/skeleton";

export function SettingsPanelSkeleton() {
  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <CardSkeleton />
      <CardSkeleton />
    </div>
  );
}

function CardSkeleton() {
  return (
    <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
      <div className="mb-5 flex items-center gap-3">
        <Skeleton className="size-9 rounded-md" />
        <div className="space-y-2">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-3 w-56" />
        </div>
      </div>
      <div className="space-y-4">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    </div>
  );
}
