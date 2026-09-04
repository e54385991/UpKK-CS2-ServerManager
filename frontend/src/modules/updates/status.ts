export type PluginUpdateLog = {
  readonly time: string | null;
  readonly message: string;
};

export function parsePluginStatusLog(line: string): PluginUpdateLog {
  const trimmed = line.trim();
  const space = trimmed.indexOf(" ");
  if (space <= 0) return { time: null, message: trimmed };
  const rawTime = trimmed.slice(0, space);
  const message = trimmed.slice(space + 1).trim();
  if (!message || Number.isNaN(new Date(rawTime).getTime())) {
    return { time: null, message: trimmed };
  }
  return { time: rawTime, message };
}

export function pluginStatusTone(
  state: string,
): "primary" | "ok" | "danger" | "neutral" {
  if (state === "running") return "primary";
  if (state === "completed") return "ok";
  if (state === "failed") return "danger";
  return "neutral";
}

export function pluginRunIsBusy(state: string | null | undefined): boolean {
  return state === "running";
}

export function formatStatusTime(
  value: string | null,
  formatDateTime: (value: Date) => string,
): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : formatDateTime(date);
}
