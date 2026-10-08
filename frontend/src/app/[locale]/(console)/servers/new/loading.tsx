import { Skeleton } from "@/shared/ui/skeleton";

export default function Loading() {
  return (
    <>
      <div className="mb-6 space-y-2">
        <Skeleton className="h-6 w-36" />
        <Skeleton className="h-4 w-96" />
      </div>
      <div className="grid gap-6 xl:grid-cols-2">
        <Skeleton className="h-80 rounded-lg" />
        <Skeleton className="h-80 rounded-lg" />
      </div>
    </>
  );
}
