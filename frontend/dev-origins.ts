import { networkInterfaces } from "node:os";

/**
 * Hosts that may open the Next.js *dev* server besides the advertised
 * `localhost` name. Next 16 blocks `/_next/static` and HMR from any other
 * Origin (including `127.0.0.1` and LAN IPs), which leaves the login
 * CAPTCHA stuck on “loading” because the client bundle never runs.
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
      if (addr.family === "IPv4") {
        hosts.add(host);
      }
    }
  }
  return [...hosts];
}
