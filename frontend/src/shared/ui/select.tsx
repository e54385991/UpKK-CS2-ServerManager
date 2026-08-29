import { cn } from "@/shared/lib/cn";
import type { ComponentProps } from "react";

export function Select({ className, children, ...props }: ComponentProps<"select">) {
  return (
    <select
      className={cn(
        "h-10 w-full rounded-md border border-line bg-surface px-3 text-sm text-fg shadow-sm outline-none transition-colors focus-visible:border-primary/60 focus-visible:ring-2 focus-visible:ring-primary/40 disabled:opacity-60",
        className,
      )}
      {...props}
    >
      {children}
    </select>
  );
}
