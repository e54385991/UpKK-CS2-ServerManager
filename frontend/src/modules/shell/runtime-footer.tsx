import { getTranslations } from "next-intl/server";
import { formatRuntimeLines } from "@/modules/shell/runtime";
import { loadRuntimeVersions } from "@/modules/shell/load-runtime";

export async function RuntimeFooter() {
  const t = await getTranslations("site");
  const versions = await loadRuntimeVersions();
  const lines = formatRuntimeLines(versions, {
    environmentProduction: t("environmentProduction"),
    environmentDevelopment: t("environmentDevelopment"),
    unavailable: t("runtimeUnavailable"),
  });

  return (
    <footer
      data-testid="runtime-footer"
      className="border-t border-line bg-canvas/90 px-4 py-2.5 text-[11px] leading-5 text-fg-subtle sm:px-6"
    >
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
