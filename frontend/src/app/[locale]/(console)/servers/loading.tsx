import { Skeleton } from "@/shared/ui/skeleton";
import { ServerListSkeleton } from "@/modules/servers/server-list";

export default function Loading() {
  return (
    <>
      <div className="mb-6 space-y-2">
        <Skeleton className="h-6 w-32" />
        <Skeleton className="h-4 w-72" />
      </div>
      <ServerListSkeleton />
    </>
  );
}
