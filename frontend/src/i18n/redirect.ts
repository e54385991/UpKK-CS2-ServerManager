import { redirect as nextRedirect } from "next/navigation";
import type { Route } from "next";
import type { PublicRoute } from "./public-route";

/** Proxy maps the typed public URL to the chosen internal locale route. */
export function redirect(href: PublicRoute, type?: Parameters<typeof nextRedirect>[1]): never {
  nextRedirect(href as Route, type);
}
