import type { DiskSpace } from "@/modules/servers/types";

/**
 * How much headroom a CS2 install needs. A full `app_update 730` download plus
 * the temporary depot files is roughly 40 GB, and SteamCMD fails part-way when
 * the filesystem runs out mid-download, so warn well before that.
 */
export const DISK_CRITICAL_GB = 5;
export const DISK_LOW_GB = 15;
export const DISK_CRITICAL_PERCENT = 95;
export const DISK_LOW_PERCENT = 90;

export type DiskHealth = "unknown" | "ok" | "low" | "critical";

/**
 * Classify a cached snapshot.
 *
 * Free bytes are authoritative whenever the host reported them: 4 % free on a
 * 4 TB array still leaves room for an update, while 20 % free on a 40 GB VPS
 * does not. The used percentage is only a fallback for hosts whose `df` output
 * did not yield a free-space figure.
 */
export function diskHealth(disk: DiskSpace | null | undefined): DiskHealth {
  if (!disk || !disk.cached) return "unknown";
  const { availableGb, usedPercent } = disk;
  if (availableGb != null) {
    if (availableGb < DISK_CRITICAL_GB) return "critical";
    return availableGb < DISK_LOW_GB ? "low" : "ok";
  }
  if (usedPercent == null) return "unknown";
  if (usedPercent >= DISK_CRITICAL_PERCENT) return "critical";
  return usedPercent >= DISK_LOW_PERCENT ? "low" : "ok";
}

export function diskHealthTone(health: DiskHealth): "ok" | "warn" | "danger" | "neutral" {
  if (health === "ok") return "ok";
  if (health === "low") return "warn";
  if (health === "critical") return "danger";
  return "neutral";
}
