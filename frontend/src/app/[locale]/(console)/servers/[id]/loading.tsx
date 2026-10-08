import { Skeleton } from "@/shared/ui/skeleton";

export default function Loading() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-4 w-40" />
      <Skeleton className="h-40 max-w-3xl rounded-lg" />
    </div>
  );
}
