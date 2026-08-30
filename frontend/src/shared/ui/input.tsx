import { cn } from "@/shared/lib/cn";
import type { ComponentProps } from "react";

export function Input({ className, ...props }: ComponentProps<"input">) {
  return (
    <input
      className={cn(
        "h-10 w-full rounded-md border border-line bg-surface px-3 text-sm text-fg shadow-sm outline-none transition-colors placeholder:text-fg-subtle focus-visible:border-primary/60 focus-visible:ring-2 focus-visible:ring-primary/40 disabled:opacity-60",
        className,
      )}
      {...props}
    />
  );
}

export function Label({ className, ...props }: ComponentProps<"label">) {
  return (
    <label
      className={cn(
        "mb-1.5 block text-sm font-medium text-fg-muted",
        className,
      )}
      {...props}
    />
  );
}
