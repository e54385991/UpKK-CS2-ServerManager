"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import type { components } from "@/shared/api/schema";
import { verifyGitHubToken } from "@/modules/plugins/ai-import-actions";
import { Button } from "@/shared/ui/button";

export function GitHubTokenCheck({ disabled, initial }: { disabled: boolean; initial?: components["schemas"]["GitHubTokenVerificationView"] | null }) {
  const t = useTranslations("plugins.aiImport");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<components["schemas"]["GitHubTokenVerificationView"] | null>(initial ?? null);
  const [error, setError] = useState("");
  return <div className="mt-3 space-y-2 text-sm">
    <Button variant="outline" type="button" disabled={disabled || busy} onClick={async () => {
      setBusy(true); setError("");
      try { const response = await verifyGitHubToken(); if (response.ok) setResult(response.data); else setError(t("requestFailed")); }
      finally { setBusy(false); }
    }}>{busy ? t("verifying") : t("verifyToken")}</Button>
    {disabled && <p className="text-xs text-fg-muted">{t("saveTokenFirst")}</p>}
    {result && !disabled && <div className={result.valid ? "text-ok" : "text-danger"} role="status">
      <p>{result.valid ? t("verified") : t("invalid")} {result.account}</p>
      <p className="text-xs">{result.message}</p>
      {result.checked_at && <p className="text-xs">{new Date(result.checked_at).toLocaleString()}</p>}
      {result.valid && <p className="text-xs">Core: {result.core_remaining} · Search: {result.search_remaining}</p>}
      {result.core_reset && <p className="text-xs">Core {t("retryAt")}: {new Date(result.core_reset * 1000).toLocaleString()}</p>}
      {result.search_reset && <p className="text-xs">Search {t("retryAt")}: {new Date(result.search_reset * 1000).toLocaleString()}</p>}
    </div>}
    {error && <p role="alert" className="text-danger">{error}</p>}
  </div>;
}
