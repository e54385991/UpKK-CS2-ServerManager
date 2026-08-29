const STORAGE_KEY = "upkk-initialized-hosts";

export function normalizeHost(host: string): string {
  return host.trim().toLowerCase();
}

export function hostsMatch(left: string, right: string): boolean {
  return normalizeHost(left) === normalizeHost(right) && normalizeHost(left) !== "";
}

export function readRememberedInitializedHosts(): readonly string[] {
  if (typeof sessionStorage === "undefined") return [];
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is string => typeof item === "string");
  } catch {
    return [];
  }
}

export function rememberInitializedHost(host: string): void {
  const normalized = normalizeHost(host);
  if (!normalized || typeof sessionStorage === "undefined") return;
  const next = Array.from(
    new Set([...readRememberedInitializedHosts(), normalized]),
  );
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next));
}

export function isRememberedInitializedHost(host: string): boolean {
  const normalized = normalizeHost(host);
  return (
    normalized !== "" &&
    readRememberedInitializedHosts().some((item) => item === normalized)
  );
}

export function isHostReadyToAdd(
  host: string,
  savedHosts: readonly { host: string }[],
  markedHost?: string,
): boolean {
  if (!normalizeHost(host)) return false;
  if (markedHost && hostsMatch(host, markedHost)) return true;
  if (isRememberedInitializedHost(host)) return true;
  return savedHosts.some((item) => hostsMatch(item.host, host));
}

export function setupWizardHref(input: {
  name?: string;
  host?: string;
  sshPort?: number;
  sshUser?: string;
}): string {
  const params = new URLSearchParams({ tab: "setup", requireInit: "1" });
  const name = input.name?.trim();
  const host = input.host?.trim();
  const sshUser = input.sshUser?.trim();
  if (name) params.set("name", name);
  if (host) params.set("host", host);
  if (input.sshPort && input.sshPort > 0) params.set("sshPort", String(input.sshPort));
  if (sshUser) params.set("sshUser", sshUser);
  return `/servers/new?${params.toString()}`;
}

export function addServerAfterSetupHref(input: {
  host: string;
  initializedServerId?: string | null;
}): string {
  const params = new URLSearchParams({ initialized: "1", host: input.host });
  if (input.initializedServerId) params.set("from", input.initializedServerId);
  return `/servers/new?${params.toString()}`;
}
