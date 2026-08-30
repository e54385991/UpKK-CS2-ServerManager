export type RuntimeEnvironment = "production" | "development";

export type RuntimeVersions = {
  readonly environment: RuntimeEnvironment;
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

export function formatRuntimeLines(
  versions: RuntimeVersions,
  labels: {
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
