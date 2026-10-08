import { Skeleton } from "@/shared/ui/skeleton";

export default function Loading() {
  return <div className="max-w-4xl space-y-4 rounded-lg border border-line bg-surface p-5"><Skeleton className="h-6 w-48" /><Skeleton className="h-24 w-full" /><Skeleton className="h-40 w-full" /></div>;
}
