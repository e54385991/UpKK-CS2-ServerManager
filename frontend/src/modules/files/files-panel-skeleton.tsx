import { Folder } from "lucide-react";
import { Skeleton } from "@/shared/ui/skeleton";

export function FilesPanelSkeleton() {
  return <div className="space-y-4">
    <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
      <div className="mb-4 flex items-center gap-2"><Folder className="size-4 text-fg-subtle" /><Skeleton className="h-4 w-32" /></div>
      <Skeleton className="mb-2 h-8 w-full" /><Skeleton className="h-8 w-2/3" />
    </div>
  </div>;
}
