"use client";

import Link from "next/link";
import { useState, type ComponentProps, type ReactNode } from "react";
import type { Route } from "next";
import { LinkPendingHint } from "@/shared/ui/link-pending-hint";

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
