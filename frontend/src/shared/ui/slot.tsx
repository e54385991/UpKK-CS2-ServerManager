import {
  Children,
  cloneElement,
  isValidElement,
  type HTMLAttributes,
  type ReactElement,
} from "react";
import { cn } from "@/shared/lib/cn";

/**
 * Minimal `asChild` slot: merge the given props onto a single child element
 * instead of rendering a wrapper. Keeps polymorphic components (Button asChild
 * -> Link) dependency-free.
 */
export function Slot({
  children,
  className,
  ...props
}: HTMLAttributes<HTMLElement>) {
  if (!isValidElement(children)) {
    return null;
  }
  const child = Children.only(children) as ReactElement<
    HTMLAttributes<HTMLElement>
  >;
  return cloneElement(child, {
    ...props,
    ...child.props,
    className: cn(className, child.props.className),
  });
}
