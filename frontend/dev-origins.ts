import { networkInterfaces } from "node:os";

/**
 * Hosts that may open the Next.js *dev* server besides the advertised
 * `localhost` name. Next 16 blocks `/_next/static` and HMR from any other
 * Origin (including `127.0.0.1` and LAN IPs), which leaves the login
 * CAPTCHA stuck on “loading” because the client bundle never runs.
 *
 * Sources, in order:
 * - localhost aliases
 * - this process's non-loopback IPv4 addresses
 * - `ALLOWED_DEV_ORIGINS` (comma-separated extra hosts)
 * - hostnames from `INTERNAL_API_URL` / `PUBLIC_APP_URL` (bind addresses
 *   such as `0.0.0.0` are ignored)
 */
export function lanDevOrigins(): string[] {
  const extras = (process.env.ALLOWED_DEV_ORIGINS ?? "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  const hosts = new Set<string>(["127.0.0.1", "localhost", "[::1]", "::1", ...extras]);

  for (const addrs of Object.values(networkInterfaces())) {
    for (const addr of addrs ?? []) {
      if (addr.internal) continue;
      const host = addr.address.split("%")[0];
      if (!host) continue;
      if (isIpv4Family(addr.family)) {
        hosts.add(host);
      }
    }
  }

  for (const raw of [
    process.env.INTERNAL_API_URL,
    process.env.PUBLIC_APP_URL,
  ]) {
    const host = hostFromOriginUrl(raw);
    if (host) hosts.add(host);
  }

  return [...hosts];
}

function isIpv4Family(family: string | number): boolean {
  return family === "IPv4" || family === 4;
}

function hostFromOriginUrl(raw: string | undefined): string | undefined {
  if (!raw?.trim()) return undefined;
  try {
    const url = new URL(raw.trim());
    if (url.hostname === "0.0.0.0" || url.hostname === "::" || url.hostname === "[::]") {
      return undefined;
    }
    return url.hostname;
  } catch {
    return undefined;
  }
}
