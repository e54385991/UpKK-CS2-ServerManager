import { getTranslations } from "next-intl/server";
import { formatRuntimeLines } from "@/modules/shell/runtime";
import { loadRuntimeVersions } from "@/modules/shell/load-runtime";
import { GithubIcon } from "@/shared/ui/github-icon";

const PROJECT_URL = "https://github.com/e54385991/upkK-CS2-ServerManager/";

export async function RuntimeFooter() {
  const [t, versions] = await Promise.all([
    getTranslations("site"),
    loadRuntimeVersions(),
  ]);
  const lines = formatRuntimeLines(versions, {
    frontend: t("frontendBuild"),
    backend: t("backendBuild"),
    version: t("buildVersion"),
    commit: t("buildCommit"),
    buildTime: t("buildTime"),
    environmentProduction: t("environmentProduction"),
    environmentDevelopment: t("environmentDevelopment"),
    unavailable: t("runtimeUnavailable"),
  });

  return (
    <footer
      data-testid="runtime-footer"
      className="border-t border-line bg-canvas/90 px-4 py-2.5 text-[11px] leading-5 text-fg-subtle sm:px-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          {lines.map((line, index) => (
            <span key={line.key} className="inline-flex items-center gap-2">
              {index > 0 ? <span aria-hidden className="text-line-strong">·</span> : null}
              <span className="font-mono tabular-nums">
                {line.label ? `${line.label} ${line.value}` : line.value}
              </span>
            </span>
          ))}
        </div>
        <a
          href={PROJECT_URL}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={t("githubProject")}
          title={t("githubProject")}
          data-testid="github-project-link"
          className="inline-flex shrink-0 items-center rounded-sm text-fg-subtle transition-colors hover:text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          <GithubIcon className="size-4" />
        </a>
      </div>
    </footer>
  );
}

export function RuntimeFooterSkeleton() {
  return (
    <footer className="border-t border-line bg-canvas/90 px-4 py-2.5 sm:px-6">
      <div className="h-5 w-full max-w-xl rounded-sm bg-surface-raised" />
    </footer>
  );
}
