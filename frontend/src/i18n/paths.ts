import { LOCALES } from "./config.ts";

const EXCLUDED_PREFIXES = ["api", "health", "static", "_next", "ai-assistant",
  "ai-provider-test", "ai-settings", "ai-stream", "cleanup-stream", "files-upload",
  "ops-stream", "passkeys", "plugin-config-scan", "server-ops", "server-record", "setup-stream"];

export function normalizePublicPath(pathname: string) {
  const segments = pathname.split("/").map(segment => {
    try {
      const decoded = decodeURIComponent(segment);
      return decoded.includes("/") ? segment : decoded;
    } catch { return segment; }
  });
  const prefixed = LOCALES.some(locale => segments[1] === locale);
  if (prefixed) segments.splice(1, 1);
  return { pathname: segments.join("/") || "/", prefixed };
}

export function isConsolePath(pathname: string) {
  return /^\/(overview|servers|plugins|assistant|audit|settings|live-console)(\/|$)/.test(pathname);
}

export function excludesLocaleRewrite(pathname: string) {
  return pathname.includes(".") || EXCLUDED_PREFIXES.includes(pathname.split("/")[1] ?? "");
}

export function internalLocalePaths(pathname: string) {
  return LOCALES.map(locale => `/${locale}${pathname === "/" ? "" : pathname}`);
}

export function loginReturnHref(publicPath: string) {
  const safe = publicPath.startsWith("/") && !publicPath.startsWith("//") ? publicPath : "/overview";
  return safe === "/overview" ? "/login" : `/login?next=${encodeURIComponent(safe)}`;
}
