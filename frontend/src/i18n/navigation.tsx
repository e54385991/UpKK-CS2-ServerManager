import { createNavigation } from "next-intl/navigation";
import type { ComponentProps } from "react";
import type { UrlObject } from "url";
import { routing } from "./routing";
import type { PublicPath, PublicRoute } from "./public-route";

const navigation = createNavigation(routing);
type LinkProps = Omit<ComponentProps<typeof navigation.Link>, "href"> & {
  href: PublicRoute | (Omit<UrlObject, "pathname"> & { pathname: PublicPath });
};
type Router = ReturnType<typeof navigation.useRouter>;
type PublicRouter = Omit<Router, "push" | "replace" | "prefetch"> & {
  push: (href: PublicRoute, options?: Parameters<Router["push"]>[1]) => void;
  replace: (href: PublicRoute, options?: Parameters<Router["replace"]>[1]) => void;
  prefetch: (href: PublicRoute, options?: Parameters<Router["prefetch"]>[1]) => void;
};

export function Link(props: LinkProps) {
  return <navigation.Link {...props} />;
}

export const usePathname = navigation.usePathname;
export function useRouter(): PublicRouter {
  return navigation.useRouter();
}
