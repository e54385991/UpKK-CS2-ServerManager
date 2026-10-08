import type { AppRoutes } from "../../.next/types/routes";

type StripLocale<T> = T extends "/[locale]" ? "/"
  : T extends `/[locale]${infer Path}` ? Path : never;
type ExpandParams<T extends string> = T extends `${infer Head}[${string}]${infer Tail}`
  ? `${Head}${string}${ExpandParams<Tail>}` : T;
type PagePath = StripLocale<Exclude<AppRoutes, "/[locale]/[...rest]">>;

/** The public URL contract is derived from next typegen, never a second route list. */
export type PublicPath = ExpandParams<PagePath>;
export type PublicRoute = PublicPath | `${PublicPath}?${string}` | `${PublicPath}#${string}`;
