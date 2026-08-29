"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useTranslations } from "next-intl";
import { TriangleAlert, WandSparkles } from "lucide-react";
import { generateGsltAction, refreshProfileAction } from "@/modules/profile/actions";
import { Button } from "@/shared/ui/button";
import { CaptchaField, useCaptcha } from "@/shared/ui/captcha-field";
import { Dialog } from "@/shared/ui/dialog";
import { Input, Label } from "@/shared/ui/input";
import { cn } from "@/shared/lib/cn";

const STEAM_GSLT_MANAGE_URL = "https://steamcommunity.com/dev/managegameservers";

export function GsltTokenField({
  id,
  name,
  label,
  value,
  serverName,
  disabled,
  className,
  onChange,
}: {
  id: string;
  name: string;
  label: string;
  value: string;
  serverName?: string;
  disabled?: boolean;
  className?: string;
  onChange: (value: string) => void;
}) {
  const t = useTranslations("serverConfig");
  const [open, setOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  return (
    <div className={cn("space-y-1.5", className)} data-testid="gslt-field">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          id={id}
          name={name}
          type="password"
          autoComplete="off"
          value={value}
          disabled={disabled}
          data-testid="gslt-token-input"
          className="min-w-0 flex-1"
          placeholder={t("gsltHint")}
          onChange={(event) => onChange(event.target.value)}
        />
        <Button
          type="button"
          variant="outline"
          disabled={disabled}
          data-testid="gslt-generate"
          onClick={() => setOpen(true)}
        >
          <WandSparkles />
          {t("gsltGenerate")}
        </Button>
      </div>
      <p className="text-xs text-fg-subtle">{t("gsltInfo")}</p>
      <p className="text-xs text-fg-subtle">
        {t("gsltLink")}:{" "}
        <a
          href={STEAM_GSLT_MANAGE_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary underline-offset-2 hover:underline"
        >
          {t("gsltSteamManage")}
        </a>
      </p>
      <p className="text-xs text-fg-subtle">
        {t("gsltGenerateInfo")}{" "}
        <Link
          href={"/settings/profile" as Route}
          className="text-primary underline-offset-2 hover:underline"
        >
          {t("gsltProfileLink")}
        </Link>
      </p>
      {notice ? <p className="text-sm text-ok">{notice}</p> : null}
      {open ? (
        <GsltGenerateDialog
          serverName={serverName}
          onClose={() => setOpen(false)}
          onGenerated={(token) => {
            onChange(token);
            setNotice(t("gsltGenerated"));
            setOpen(false);
          }}
        />
      ) : null}
    </div>
  );
}

function GsltGenerateDialog({
  serverName,
  onClose,
  onGenerated,
}: {
  serverName?: string;
  onClose: () => void;
  onGenerated: (token: string) => void;
}) {
  const t = useTranslations("serverConfig");
  const tp = useTranslations("profile");
  const captcha = useCaptcha();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [missingKey, setMissingKey] = useState(false);

  useEffect(() => {
    let active = true;
    void refreshProfileAction().then((profile) => {
      if (!active) return;
      if (profile.ok && !profile.data.hasSteamApiKey) {
        setMissingKey(true);
        setError(t("gsltMissingKey"));
      }
    });
    return () => {
      active = false;
    };
  }, [t]);

  async function onGenerate() {
    if (!captcha.ready || missingKey) return;
    setPending(true);
    setError(null);
    const result = await generateGsltAction({
      serverName,
      captchaToken: captcha.token,
      captchaCode: captcha.code.trim(),
    });
    setPending(false);
    if (!result.ok) {
      setError(result.error || t("gsltFailed"));
      if (result.error.includes("Steam API key not set")) {
        setMissingKey(true);
      }
      captcha.refresh();
      return;
    }
    onGenerated(result.data.loginToken);
  }

  return (
    <Dialog
      open
      title={t("gsltGenerateTitle")}
      description={t("gsltGenerateHelp")}
      closeLabel={t("gsltClose")}
      className="max-w-lg"
      onClose={onClose}
      footer={
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>
            {t("gsltClose")}
          </Button>
          <Button
            type="button"
            data-testid="gslt-generate-confirm"
            disabled={pending || missingKey || !captcha.ready || !captcha.code.trim()}
            onClick={() => void onGenerate()}
          >
            <WandSparkles />
            {pending ? t("gsltGenerating") : t("gsltGenerate")}
          </Button>
        </div>
      }
    >
      <div className="space-y-4" data-testid="gslt-dialog">
        {error ? (
          <p className="flex items-start gap-2 text-sm text-danger">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            <span>
              {error}
              {missingKey ? (
                <>
                  {" "}
                  <Link
                    href={"/settings/profile" as Route}
                    className="underline underline-offset-2"
                  >
                    {t("gsltProfileLink")}
                  </Link>
                </>
              ) : null}
            </span>
          </p>
        ) : null}
        <CaptchaField
          id="gslt-captcha"
          label={tp("captcha")}
          placeholder={tp("captchaPlaceholder")}
          refreshLabel={tp("refreshCaptcha")}
          loadingLabel={tp("loading")}
          captcha={captcha}
        />
      </div>
    </Dialog>
  );
}
