"use client";

import { useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/shared/ui/button";
import { Input, Label } from "@/shared/ui/input";

export function GmailSetupGuide() {
  const t = useTranslations("settings");
  const [copied, setCopied] = useState(false);
  const redirectPath = "/api/gmail-oauth/callback";

  async function copyUri() {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}${redirectPath}`);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <details className="rounded-md border border-line px-3 py-2">
      <summary className="cursor-pointer text-sm font-medium text-fg">{t("gmail.guide.title")}</summary>
      <div className="mt-3 space-y-3 text-xs text-fg-muted">
        <div><p className="font-medium text-fg">{t("gmail.guide.step1Title")}</p><ol className="mt-1 list-decimal space-y-1 pl-4"><li>{t("gmail.guide.step1a")}</li><li>{t("gmail.guide.step1b")}</li><li>{t("gmail.guide.step1c")}</li></ol></div>
        <div><p className="font-medium text-fg">{t("gmail.guide.step2Title")}</p><ol className="mt-1 list-decimal space-y-1 pl-4"><li>{t("gmail.guide.step2a")}</li><li>{t("gmail.guide.step2b")}</li><li>{t("gmail.guide.step2c")}</li></ol><div className="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-warn/30 bg-warn-muted/30 px-3 py-2"><code className="min-w-0 flex-1 break-all text-fg">{redirectPath}</code><Button type="button" size="sm" variant="outline" onClick={() => void copyUri()}>{copied ? t("gmail.guide.copied") : t("gmail.guide.copy")}</Button></div></div>
        <div><p className="font-medium text-fg">{t("gmail.guide.step3Title")}</p><ol className="mt-1 list-decimal space-y-1 pl-4"><li>{t("gmail.guide.step3a")}</li><li>{t("gmail.guide.step3b")}</li><li>{t("gmail.guide.step3c")}</li></ol></div>
        <p>{t("gmail.guide.notes")}</p>
      </div>
    </details>
  );
}

export function Field({ label, htmlFor, hint, children }: { label?: string; htmlFor?: string; hint?: string; children: ReactNode }) {
  return <div>{label ? <Label htmlFor={htmlFor}>{label}</Label> : null}{children}{hint ? <p className="mt-1.5 text-xs text-fg-subtle">{hint}</p> : null}</div>;
}
