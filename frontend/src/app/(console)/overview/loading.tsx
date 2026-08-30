import { Skeleton } from "@/shared/ui/skeleton";
import { OverviewStatsSkeleton } from "@/modules/overview/overview-stats";

export default function Loading() {
  return (
    <>
      <div className="mb-6 space-y-2">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-4 w-80" />
      </div>
      <OverviewStatsSkeleton />
    </>
  );
}
