"use client";

import { useSyncExternalStore } from "react";
import { useLinkStatus } from "next/link";
import { usePathname } from "next/navigation";
import { getLinkPendingHref, subscribeLinkPending } from "@/shared/ui/nav-pending";

/**
 * Fixed-size pending hint for a `<Link>`. Always rendered so toggling
 * `pending` cannot shift the label; `useLinkStatus` only works in a
 * descendant of `Link`. Click handlers also mark the target immediately so
 * feedback does not wait on the App Router starting a transition.
 */
export function LinkPendingHint({ href }: { href: string }) {
  const { pending } = useLinkStatus();
  const pathname = usePathname();
  const optimistic = useSyncExternalStore(
    subscribeLinkPending,
    () => getLinkPendingHref() === href && pathname !== href,
    () => false,
  );
  const active = pending || optimistic;
  return (
    <span
      aria-hidden
      data-testid="link-pending-hint"
      data-pending-href={href}
      data-pending={active ? "true" : "false"}
      className={
        active
          ? "inline-block size-1.5 shrink-0 animate-pulse rounded-full bg-current opacity-70"
          : "inline-block size-1.5 shrink-0 rounded-full bg-current opacity-0"
      }
    />
  );
}
