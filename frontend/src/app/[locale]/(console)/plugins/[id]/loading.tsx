import { Skeleton } from "@/shared/ui/skeleton";

export default function Loading() {
  return (
    <>
      <div className="mb-6 space-y-2">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-4 w-80" />
      </div>
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(20rem,24rem)]">
        <Skeleton className="h-64 rounded-lg" />
        <Skeleton className="h-56 rounded-lg" />
      </div>
    </>
  );
}