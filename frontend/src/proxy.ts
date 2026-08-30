import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/modules/auth/session";

/**
 * Edge guard for the authenticated console (Next 16 `proxy` convention). This is
 * a cheap presence check on the session cookie to avoid a server round-trip for
 * obviously-anonymous visitors; the layout still validates the session against
 * the backend. Public routes and the API proxy are excluded via the matcher.
 */
export function proxy(request: NextRequest) {
  const hasSession = request.cookies.has(SESSION_COOKIE);
  const { pathname, search } = request.nextUrl;

  if (!hasSession) {
    const loginUrl = new URL("/login", request.url);
    if (pathname !== "/overview") {
      loginUrl.searchParams.set("next", `${pathname}${search}`);
    }
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  // Guard the console areas only. Public pages, the API proxy, Next internals,
  // and static assets are intentionally excluded.
  matcher: [
    "/overview/:path*",
    "/servers/:path*",
    "/plugins/:path*",
    "/assistant/:path*",
    "/audit/:path*",
    "/settings/:path*",
    "/live-console/:path*",
  ],
};
