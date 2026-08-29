export type SshPoolStats = {
  readonly connections: number;
  readonly inUse: number;
  readonly idle: number;
  readonly leases: number;
  readonly draining: number;
  readonly idleTimeout: number;
  readonly maxLifetime: number;
  readonly keepaliveInterval: number;
  readonly keepaliveCountMax: number;
};
