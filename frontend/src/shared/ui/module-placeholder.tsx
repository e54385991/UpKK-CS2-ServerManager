import { Construction } from "lucide-react";
import { Card } from "@/shared/ui/card";
import type { ReactNode } from "react";

/**
 * Placeholder for console modules whose full implementation is scheduled in a
 * later delivery phase. Communicates intent clearly instead of rendering a
 * blank route.
 */
export function ModulePlaceholder({
  phase,
  children,
}: {
  phase: string;
  children: ReactNode;
}) {
  return (
    <Card className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
      <span className="flex size-12 items-center justify-center rounded-full bg-surface-overlay text-fg-subtle">
        <Construction className="size-6" />
      </span>
      <div className="max-w-md space-y-1">
        <p className="text-sm font-medium text-fg">{phase}</p>
        <p className="text-sm text-fg-muted">{children}</p>
      </div>
    </Card>
  );
}
