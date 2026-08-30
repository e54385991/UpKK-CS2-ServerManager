"use client";

import { useState, type FormEvent } from "react";
import { useTranslations } from "next-intl";
import { saveS3Action, testS3Action } from "@/modules/profile/actions";
import type { ProfileS3Settings } from "@/modules/profile/types";
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
import { Input, Label } from "@/shared/ui/input";
import { Switch } from "@/shared/ui/switch";

export function S3Form({ initial }: { initial: ProfileS3Settings }) {
  const t = useTranslations("profile");
  const captcha = useCaptcha();
  const [settings, setSettings] = useState(initial);
  const [enabled, setEnabled] = useState(initial.enabled);
  const [endpointUrl, setEndpointUrl] = useState(initial.endpointUrl ?? "");
  const [region, setRegion] = useState(initial.region ?? "");
  const [bucket, setBucket] = useState(initial.bucket ?? "");
  const [prefix, setPrefix] = useState(initial.prefix ?? "");
  const [accessKeyId, setAccessKeyId] = useState(initial.accessKeyId ?? "");
  const [secret, setSecret] = useState("");
  const [useSsl, setUseSsl] = useState(initial.useSsl);
  const [retention, setRetention] = useState(String(initial.retentionCount));
  const [clearSecret, setClearSecret] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  async function onSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!captcha.ready) return;
    setPending("save");
    setBanner(null);
    const parsed = Number(retention);
    const result = await saveS3Action({
      enabled,
      endpointUrl: endpointUrl.trim() || null,
      region: region.trim() || null,
      bucket: bucket.trim() || null,
      accessKeyId: accessKeyId.trim() || null,
      secretAccessKey: secret.trim() || undefined,
      prefix: prefix.trim() || null,
      useSsl,
      retentionCount: Number.isInteger(parsed) ? parsed : settings.retentionCount,
      clearSecret,
      captchaToken: captcha.token,
      captchaCode: captcha.code.trim(),
    });
    setPending(null);
    captcha.refresh();
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setSettings(result.data);
    setSecret("");
    setClearSecret(false);
    setBanner(t("s3Saved"));
  }

  async function onTest() {
    setPending("test");
    setBanner(null);
    const result = await testS3Action();
    setPending(null);
    setBanner(result.ok ? result.data.message : result.error || t("failed"));
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle>{t("s3Title")}</CardTitle>
            <CardDescription>{t("s3Help")}</CardDescription>
          </div>
          <Badge tone={settings.isConfigured ? "ok" : "neutral"}>
            {settings.isConfigured ? t("s3Configured") : t("s3NotConfigured")}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSave} className="space-y-4">
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="s3-enabled" className="mb-0">
              {t("s3Enabled")}
            </Label>
            <Switch
              id="s3-enabled"
              label={t("s3Enabled")}
              checked={enabled}
              onCheckedChange={setEnabled}
            />
          </div>
          <p className="text-xs text-fg-subtle">{t("s3CompatibleHint")}</p>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="s3-endpoint">{t("s3Endpoint")}</Label>
              <Input
                id="s3-endpoint"
                type="url"
                value={endpointUrl}
                onChange={(event) => setEndpointUrl(event.target.value)}
                placeholder={t("s3EndpointPlaceholder")}
              />
            </div>
            <div>
              <Label htmlFor="s3-region">{t("s3Region")}</Label>
              <Input
                id="s3-region"
                value={region}
                onChange={(event) => setRegion(event.target.value)}
                placeholder={t("s3RegionPlaceholder")}
              />
            </div>
            <div>
              <Label htmlFor="s3-bucket">{t("s3Bucket")}</Label>
              <Input
                id="s3-bucket"
                value={bucket}
                onChange={(event) => setBucket(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="s3-prefix">{t("s3Prefix")}</Label>
              <Input
                id="s3-prefix"
                value={prefix}
                onChange={(event) => setPrefix(event.target.value)}
                placeholder="cs2-backups"
              />
            </div>
            <div>
              <Label htmlFor="s3-access">{t("s3AccessKey")}</Label>
              <Input
                id="s3-access"
                value={accessKeyId}
                onChange={(event) => setAccessKeyId(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="s3-secret">{t("s3SecretKey")}</Label>
              <Input
                id="s3-secret"
                type="password"
                autoComplete="new-password"
                disabled={clearSecret}
                value={secret}
                onChange={(event) => setSecret(event.target.value)}
                placeholder={t("s3SecretPlaceholder")}
              />
              <p className="mt-1.5 text-xs text-fg-subtle">
                {settings.hasSecret ? t("s3SecretSet") : t("s3SecretMissing")}
              </p>
            </div>
            <div>
              <Label htmlFor="s3-retention">{t("s3RetentionCount")}</Label>
              <Input
                id="s3-retention"
                type="number"
                min={1}
                max={10000}
                value={retention}
                onChange={(event) => setRetention(event.target.value)}
              />
              <p className="mt-1.5 text-xs text-fg-subtle">{t("s3RetentionHint")}</p>
            </div>
          </div>
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="s3-ssl" className="mb-0">
              {t("s3UseSsl")}
            </Label>
            <Switch
              id="s3-ssl"
              label={t("s3UseSsl")}
              checked={useSsl}
              onCheckedChange={setUseSsl}
            />
          </div>
          {settings.hasSecret ? (
            <label className="flex items-center gap-2 text-sm text-fg-muted">
              <input
                type="checkbox"
                className="size-4 rounded border-line accent-primary"
                checked={clearSecret}
                onChange={(event) => {
                  setClearSecret(event.target.checked);
                  if (event.target.checked) setSecret("");
                }}
              />
              {t("s3ClearSecret")}
            </label>
          ) : null}
          <CaptchaField
            id="s3-captcha"
            label={t("captcha")}
            placeholder={t("captchaPlaceholder")}
            refreshLabel={t("refreshCaptcha")}
            loadingLabel={t("loading")}
            captcha={captcha}
          />
          {banner ? (
            <p className="text-sm text-fg-muted" role="status">
              {banner}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={Boolean(pending) || !captcha.ready}>
              {pending === "save" ? t("saving") : t("s3Save")}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={Boolean(pending)}
              onClick={() => void onTest()}
            >
              {pending === "test" ? t("testing") : t("s3Test")}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
