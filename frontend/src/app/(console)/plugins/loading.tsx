import { Skeleton } from "@/shared/ui/skeleton";
import { MarketCatalogSkeleton } from "@/modules/plugins/market-catalog";

export default function Loading() {
  return (
    <>
      <div className="mb-6 space-y-2">
        <Skeleton className="h-6 w-32" />
        <Skeleton className="h-4 w-96" />
      </div>
      <MarketCatalogSkeleton />
    </>
  );
}