"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Copy } from "lucide-react";
import {
  generateApiKeyAction,
  refreshApiKeyAction,
  revokeApiKeyAction,
} from "@/modules/profile/actions";
import type { ProfileSettings } from "@/modules/profile/types";
import { confirm } from "@/shared/feedback";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { CaptchaField, useCaptcha } from "@/shared/ui/captcha-field";

export function ApiKeyForm({ initial }: { initial: ProfileSettings }) {
  const t = useTranslations("profile");
  const captcha = useCaptcha();
  const [hasKey, setHasKey] = useState(initial.hasApiKey);
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  async function reveal() {
    setPending("reveal");
    setBanner(null);
    const result = await refreshApiKeyAction();
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setApiKey(result.data.apiKey);
    setHasKey(true);
  }

  async function rotate() {
    if (!captcha.ready) return;
    setPending("rotate");
    setBanner(null);
    const result = await generateApiKeyAction({
      captchaToken: captcha.token,
      captchaCode: captcha.code.trim(),
    });
    setPending(null);
    captcha.refresh();
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setApiKey(result.data.apiKey);
    setHasKey(true);
    setBanner(t("apiKeyGenerated"));
  }

  async function revoke() {
    if (!(await confirm(t("apiKeyRevokeConfirm")))) return;
    setPending("revoke");
    setBanner(null);
    const result = await revokeApiKeyAction();
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setApiKey(null);
    setHasKey(false);
    setBanner(result.data.message || t("apiKeyRevoked"));
  }

  async function copyKey() {
    if (!apiKey) return;
    try {
      await navigator.clipboard.writeText(apiKey);
      setBanner(t("copied"));
    } catch {
      setBanner(t("copyFailed"));
    }
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle>{t("apiKeyTitle")}</CardTitle>
            <CardDescription>{t("apiKeyHelp")}</CardDescription>
          </div>
          <Badge tone={hasKey ? "ok" : "neutral"}>
            {hasKey ? t("configured") : t("notConfigured")}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {apiKey ? (
          <div className="rounded-md border border-line bg-surface-raised/40 px-4 py-3">
            <p className="mb-2 text-xs font-medium text-fg-muted">{t("currentApiKey")}</p>
            <div className="flex items-start gap-2">
              <code className="min-w-0 flex-1 break-all rounded bg-canvas px-2 py-1 text-xs text-fg">
                {apiKey}
              </code>
              <Button type="button" variant="outline" size="sm" onClick={() => void copyKey()}>
                <Copy className="size-4" />
                {t("copy")}
              </Button>
            </div>
            <p className="mt-2 text-xs text-warn">{t("apiKeyWarning")}</p>
          </div>
        ) : (
          <p className="text-sm text-fg-muted">
            {hasKey ? t("apiKeyHidden") : t("noApiKey")}
          </p>
        )}

        <CaptchaField
          id="apikey-captcha"
          label={t("captcha")}
          placeholder={t("captchaPlaceholder")}
          refreshLabel={t("refreshCaptcha")}
          loadingLabel={t("loading")}
          captcha={captcha}
          required={false}
        />

        {banner ? (
          <p className="text-sm text-fg-muted" role="status">
            {banner}
          </p>
        ) : null}

        <div className="flex flex-wrap gap-2">
          {hasKey && !apiKey ? (
            <Button
              type="button"
              variant="secondary"
              disabled={Boolean(pending)}
              onClick={() => void reveal()}
            >
              {pending === "reveal" ? t("loading") : t("showApiKey")}
            </Button>
          ) : null}
          <Button
            type="button"
            disabled={Boolean(pending) || !captcha.ready}
            onClick={() => void rotate()}
          >
            {pending === "rotate"
              ? t("saving")
              : hasKey
                ? t("regenerateApiKey")
                : t("generateApiKey")}
          </Button>
          {hasKey ? (
            <Button
              type="button"
              variant="danger"
              disabled={Boolean(pending)}
              onClick={() => void revoke()}
            >
              {pending === "revoke" ? t("saving") : t("revokeApiKey")}
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
