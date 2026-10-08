import { Skeleton } from "@/shared/ui/skeleton";
import { AuditTableSkeleton } from "@/modules/audit/audit-table";

export default function Loading() {
  return (
    <>
      <div className="mb-6 space-y-2">
        <Skeleton className="h-6 w-28" />
        <Skeleton className="h-4 w-96" />
      </div>
      <AuditTableSkeleton />
    </>
  );
}
