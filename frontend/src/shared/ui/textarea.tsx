import { cn } from "@/shared/lib/cn";
import type { ComponentProps } from "react";

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return (
    <textarea
      className={cn(
        "min-h-28 w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-fg shadow-sm outline-none transition-colors placeholder:text-fg-subtle focus-visible:border-primary/60 focus-visible:ring-2 focus-visible:ring-primary/40 disabled:opacity-60",
        className,
      )}
      {...props}
    />
  );
}
