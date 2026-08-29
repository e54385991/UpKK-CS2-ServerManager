import { cn } from "@/shared/lib/cn";
import type { ComponentProps } from "react";

export function Skeleton({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-md bg-surface-overlay/70",
        className,
      )}
      {...props}
    />
  );
}
