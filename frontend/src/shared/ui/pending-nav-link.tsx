"use client";

import Link, { useLinkStatus } from "next/link";
import { useState, type ComponentProps, type ReactNode } from "react";
import type { Route } from "next";
import { cn } from "@/shared/lib/cn";

/**
 * Fixed-size pending hint for a `<Link>`. Always rendered so toggling
 * `pending` cannot shift the tab label; `useLinkStatus` only works in a
 * descendant of `Link`.
 */
export function LinkPendingHint({ className }: { className?: string }) {
  const { pending } = useLinkStatus();
  return (
    <span
      aria-hidden
      data-testid="link-pending-hint"
      data-pending={pending ? "true" : "false"}
      className={cn(
        "inline-block size-1.5 shrink-0 rounded-full bg-current",
        pending ? "animate-pulse opacity-70" : "opacity-0",
        className,
      )}
    />
  );
}

type PendingNavLinkProps = Omit<
  ComponentProps<typeof Link>,
  "href" | "prefetch"
> & {
  href: Route;
  prefetchOnIntent?: boolean;
  prefetch?: boolean | "auto" | null;
  children: ReactNode;
};

/**
 * Workspace / config sub-nav link. Prefetch stays off unless `prefetchOnIntent`
 * is set, in which case the default App Router prefetch starts on hover or
 * keyboard focus (not while the tab is merely in view).
 */
export function PendingNavLink({
  href,
  prefetchOnIntent = false,
  prefetch = false,
  children,
  onMouseEnter,
  onFocus,
  ...props
}: PendingNavLinkProps) {
  const [intent, setIntent] = useState(false);
  const resolvedPrefetch = prefetchOnIntent ? (intent ? null : false) : prefetch;

  return (
    <Link
      href={href}
      prefetch={resolvedPrefetch}
      onMouseEnter={(event) => {
        if (prefetchOnIntent) setIntent(true);
        onMouseEnter?.(event);
      }}
      onFocus={(event) => {
        if (prefetchOnIntent) setIntent(true);
        onFocus?.(event);
      }}
      {...props}
    >
      {children}
      <LinkPendingHint />
    </Link>
  );
}
