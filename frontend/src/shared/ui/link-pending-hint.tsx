"use client";

import { useLinkStatus } from "next/link";

/**
 * Fixed-size pending hint for a `<Link>`. Always rendered so toggling
 * `pending` cannot shift the label; `useLinkStatus` only works in a
 * descendant of `Link`. Shared by workspace tabs, the config sub-nav,
 * the desktop sidebar, and the mobile drawer.
 */
export function LinkPendingHint() {
  const { pending } = useLinkStatus();
  return (
    <span
      aria-hidden
      data-testid="link-pending-hint"
      data-pending={pending ? "true" : "false"}
      className={
        pending
          ? "inline-block size-1.5 shrink-0 animate-pulse rounded-full bg-current opacity-70"
          : "inline-block size-1.5 shrink-0 rounded-full bg-current opacity-0"
      }
    />
  );
}
