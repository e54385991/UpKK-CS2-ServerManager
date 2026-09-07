"use client";

import { useTranslations } from "next-intl";
import { TriangleAlert } from "lucide-react";
import { LinkButton } from "@/shared/ui/link-button";

export function GitHubRequestError({ error }: { error: string }) {
  const t = useTranslations("plugins.github");
  // The release API wraps upstream authentication errors in HTTP 400.
  const invalidCredentials = /\bBad credentials\b/i.test(error)
    || /Failed to fetch (?:GitHub )?releases:\s*HTTP 401\b/i.test(error);

  return (
    <div role="alert" className="flex items-start gap-2 rounded-md border border-danger/30 bg-danger-muted/50 px-3 py-2 text-sm text-danger">
      <TriangleAlert className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 space-y-2">
        <p>{invalidCredentials ? t("credentialsInvalid") : error}</p>
        {invalidCredentials ? (
          <LinkButton href="/settings/profile#profile-github-token" variant="outline" size="sm">
            {t("configureCredentials")}
          </LinkButton>
        ) : null}
      </div>
    </div>
  );
}
