import { Skeleton } from "@/shared/ui/skeleton";
import { Card } from "@/shared/ui/card";

export default function Loading() {
  return (
    <>
      <div className="mb-6 space-y-2">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-4 w-80" />
      </div>
      <Card className="space-y-3 px-6 py-8">
        <Skeleton className="h-4 w-56" />
        <Skeleton className="h-4 w-72" />
        <Skeleton className="h-10 w-36" />
      </Card>
    </>
  );
}
