import "server-only";
import { internalApiUrl } from "@/shared/config/internal-api";
import type { HealthResponseDto } from "@/shared/api/types";
import {
  nodeVersion,
  runtimeEnvironment,
  type BuildMetadata,
  type RuntimeVersions,
} from "@/modules/shell/runtime";
import appPackage from "../../../package.json";

function optionalText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  return text && text !== "unknown" ? text : null;
}

const emptyBuildMetadata = (): BuildMetadata => ({
  version: null,
  commit: null,
  buildTime: null,
});

async function loadBackendRuntime(): Promise<
  Pick<RuntimeVersions, "backend" | "python" | "fastapi">
> {
  try {
    const response = await fetch(`${internalApiUrl()}/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    if (!response.ok) {
      return { backend: emptyBuildMetadata(), python: null, fastapi: null };
    }
    const body = (await response.json()) as HealthResponseDto;
    return {
      backend: {
        version: optionalText(body.version),
        commit: optionalText(body.git_sha),
        buildTime: optionalText(body.build_time),
      },
      python: optionalText(body.python),
      fastapi: optionalText(body.fastapi),
    };
  } catch {
    return { backend: emptyBuildMetadata(), python: null, fastapi: null };
  }
}

export async function loadRuntimeVersions(): Promise<RuntimeVersions> {
  const backend = await loadBackendRuntime();
  return {
    environment: runtimeEnvironment(process.env.NODE_ENV),
    frontend: {
      version: optionalText(appPackage.version),
      commit: optionalText(process.env.APP_GIT_SHA),
      buildTime: optionalText(process.env.APP_BUILD_TIME),
    },
    backend: backend.backend,
    node: nodeVersion(process.version),
    next: appPackage.dependencies.next,
    react: appPackage.dependencies.react,
    python: backend.python,
    fastapi: backend.fastapi,
  };
}
