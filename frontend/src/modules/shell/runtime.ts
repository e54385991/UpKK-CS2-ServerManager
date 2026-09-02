export type RuntimeEnvironment = "production" | "development";

export type BuildMetadata = {
  readonly version: string | null;
  readonly commit: string | null;
  readonly buildTime: string | null;
};

export type RuntimeVersions = {
  readonly environment: RuntimeEnvironment;
  readonly frontend: BuildMetadata;
  readonly backend: BuildMetadata;
  readonly node: string;
  readonly next: string;
  readonly react: string;
  readonly python: string | null;
  readonly fastapi: string | null;
};

export type RuntimeLine = {
  readonly key: string;
  readonly label: string;
  readonly value: string;
};

export function runtimeEnvironment(nodeEnv?: string): RuntimeEnvironment {
  return nodeEnv === "production" ? "production" : "development";
}

export function nodeVersion(raw?: string): string {
  const text = (raw ?? "").trim();
  return text.startsWith("v") ? text.slice(1) : text;
}

export function shortCommit(raw?: string | null): string {
  const text = (raw ?? "").trim();
  if (!text || text.toLowerCase() === "unknown") return "";
  return /^[0-9a-f]{7,64}$/i.test(text) ? text.slice(0, 7).toLowerCase() : "";
}

export function formatBuildTime(raw?: string | null): string {
  const text = (raw ?? "").trim();
  if (!text || text.toLowerCase() === "unknown") return "";
  if (!/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:?\d{2})$/i.test(text)) return "";
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.toISOString().slice(0, 19).replace("T", " ")} UTC`;
}

function formatBuildSummary(
  metadata: BuildMetadata,
  labels: {
    readonly version: string;
    readonly commit: string;
    readonly buildTime: string;
    readonly unavailable: string;
  },
): string {
  const version = metadata.version?.trim() || labels.unavailable;
  const commit = shortCommit(metadata.commit) || labels.unavailable;
  const buildTime = formatBuildTime(metadata.buildTime) || labels.unavailable;
  return `${labels.version} ${version} · ${labels.commit} ${commit} · ${labels.buildTime} ${buildTime}`;
}

export function formatRuntimeLines(
  versions: RuntimeVersions,
  labels: {
    readonly frontend: string;
    readonly backend: string;
    readonly version: string;
    readonly commit: string;
    readonly buildTime: string;
    readonly environmentProduction: string;
    readonly environmentDevelopment: string;
    readonly unavailable: string;
  },
): readonly RuntimeLine[] {
  const environment =
    versions.environment === "production"
      ? labels.environmentProduction
      : labels.environmentDevelopment;
  return [
    { key: "environment", label: "", value: environment },
    {
      key: "frontend",
      label: labels.frontend,
      value: formatBuildSummary(versions.frontend, labels),
    },
    {
      key: "backend",
      label: labels.backend,
      value: formatBuildSummary(versions.backend, labels),
    },
    { key: "node", label: "Node", value: versions.node || labels.unavailable },
    { key: "next", label: "Next", value: versions.next || labels.unavailable },
    { key: "react", label: "React", value: versions.react || labels.unavailable },
    {
      key: "python",
      label: "Python",
      value: versions.python || labels.unavailable,
    },
    {
      key: "fastapi",
      label: "FastAPI",
      value: versions.fastapi || labels.unavailable,
    },
  ];
}
