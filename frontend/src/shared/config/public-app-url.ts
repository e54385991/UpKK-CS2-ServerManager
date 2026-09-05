/**
 * Public origin the browser uses to reach this Next.js console.
 *
 * `PUBLIC_APP_URL` is optional. Bind addresses such as `0.0.0.0` are ignored.
 * When unset (or unusable), the origin is taken from the request Host /
 * X-Forwarded-Host and proto, including the port the client actually used.
 */

const BIND_HOSTS = new Set(["0.0.0.0", "::", "[::]"]);

export function hostFromOriginUrl(raw: string | undefined): string | undefined {
  if (!raw?.trim()) return undefined;
  try {
    const url = new URL(raw.trim());
    if (BIND_HOSTS.has(url.hostname)) return undefined;
    return url.hostname;
  } catch {
    return undefined;
  }
}

export function configuredPublicAppUrl(): string | null {
  const raw = process.env["PUBLIC_APP_URL"]?.trim();
  const host = hostFromOriginUrl(raw);
  if (!raw || !host) return null;
  try {
    return new URL(raw).origin;
  } catch {
    return null;
  }
}

export function publicAppUrlFromHeaders(headerSource: Headers): string {
  const configured = configuredPublicAppUrl();
  if (configured) return configured;

  const forwardedHost = firstForwarded(headerSource.get("x-forwarded-host"));
  const host = forwardedHost || headerSource.get("host")?.trim();
  const proto =
    firstForwarded(headerSource.get("x-forwarded-proto")) ||
    (headerSource.get("x-forwarded-ssl") === "on" ? "https" : "http");

  if (!host) return "http://localhost:31800";

  try {
    const origin = new URL(`${proto}://${host}`);
    if (BIND_HOSTS.has(origin.hostname)) {
      return `http://localhost${origin.port ? `:${origin.port}` : ""}`;
    }
    return origin.origin;
  } catch {
    return "http://localhost:31800";
  }
}

function firstForwarded(value: string | null): string | undefined {
  const item = value?.split(",")[0]?.trim();
  return item || undefined;
}
