import Link from "next/link";
import type { Route } from "next";
import type { ReactNode } from "react";
import { Button, type ButtonProps } from "@/shared/ui/button";

/**
 * A `<Link>` styled as a button. Keeps client-side navigation (and prefetch)
 * while reusing the button visual variants.
 */
export function LinkButton({
  href,
  children,
  variant,
  size,
  className,
}: {
  href: Route;
  children: ReactNode;
  variant?: ButtonProps["variant"];
  size?: ButtonProps["size"];
  className?: string;
}) {
  return (
    <Button asChild variant={variant} size={size} className={className}>
      <Link href={href}>{children}</Link>
    </Button>
  );
}
