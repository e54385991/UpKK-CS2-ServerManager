import { NextResponse, type NextRequest } from "next/server";
import { hasSessionCookie } from "@/shared/auth/session-cookie";
import { LOCALE_COOKIE, resolveLocale } from "@/i18n/config";
import { excludesLocaleRewrite, isConsolePath, loginReturnHref, normalizePublicPath } from "@/i18n/paths";

export function proxy(request: NextRequest) {
  const url = new URL(request.url);
  const { pathname, prefixed } = normalizePublicPath(url.pathname);
  const publicUrl = new URL(url);
  publicUrl.pathname = pathname;
  publicUrl.searchParams.delete("_rsc");
  if (excludesLocaleRewrite(pathname)) return NextResponse.next();
  if (isConsolePath(pathname) && !hasSessionCookie(request.cookies)) {
    return NextResponse.redirect(new URL(loginReturnHref(`${pathname}${publicUrl.search}`), request.url));
  }
  if (prefixed) {
    return NextResponse.redirect(publicUrl);
  }
  const locale = resolveLocale(request.cookies.get(LOCALE_COOKIE)?.value, request.headers.get("accept-language"));
  const forwarded = new Headers(request.headers);
  forwarded.set("x-upkk-public-path", `${pathname}${publicUrl.search}`);
  url.pathname = `/${locale}${pathname === "/" ? "" : pathname}`;
  return NextResponse.rewrite(url, { request: { headers: forwarded } });
}

export const config = {
  matcher: ["/((?!api(?:/|$)|health(?:/|$)|static(?:/|$)|_next(?:/|$)|ai-assistant(?:/|$)|ai-provider-test(?:/|$)|ai-settings(?:/|$)|ai-stream(?:/|$)|cleanup-stream(?:/|$)|files-upload(?:/|$)|ops-stream(?:/|$)|passkeys(?:/|$)|plugin-config-scan(?:/|$)|server-ops(?:/|$)|server-record(?:/|$)|setup-stream(?:/|$)|.*\\..*).*)"],
};
