import "server-only";
import { internalApiUrl } from "@/shared/config/internal-api";
import {
  nodeVersion,
  runtimeEnvironment,
  type RuntimeVersions,
} from "@/modules/shell/runtime";
import appPackage from "../../../package.json";

type HealthRuntime = {
  python?: unknown;
  fastapi?: unknown;
};

function optionalText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  return text && text !== "unknown" ? text : null;
}

async function loadBackendRuntime(): Promise<Pick<RuntimeVersions, "python" | "fastapi">> {
  try {
    const response = await fetch(`${internalApiUrl()}/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    if (!response.ok) return { python: null, fastapi: null };
    const body = (await response.json()) as HealthRuntime;
    return {
      python: optionalText(body.python),
      fastapi: optionalText(body.fastapi),
    };
  } catch {
    return { python: null, fastapi: null };
  }
}

export async function loadRuntimeVersions(): Promise<RuntimeVersions> {
  const backend = await loadBackendRuntime();
  return {
    environment: runtimeEnvironment(process.env.NODE_ENV),
    node: nodeVersion(process.version),
    next: appPackage.dependencies.next,
    react: appPackage.dependencies.react,
    python: backend.python,
    fastapi: backend.fastapi,
  };
}
