"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useTranslations } from "next-intl";
import { KeyRound, TriangleAlert } from "lucide-react";
import { requestPasswordResetAction } from "@/modules/auth/actions";
import { Button } from "@/shared/ui/button";
import { CaptchaField, useCaptcha } from "@/shared/ui/captcha-field";
import { Input, Label } from "@/shared/ui/input";

export function ForgotPasswordForm() {
  const t = useTranslations("forgotPassword");
  const captcha = useCaptcha();
  const [email, setEmail] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!captcha.ready) return;
    setError(null);
    setPending(true);
    const result = await requestPasswordResetAction({
      email: email.trim(),
      captchaToken: captcha.token,
      captchaCode: captcha.code.trim(),
    });
    setPending(false);
    captcha.refresh();
    if (!result.ok) {
      setError(result.error || t("errorMessage"));
      return;
    }
    setSent(true);
  }

  if (sent) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-fg">{t("successMessage")}</p>
        <Link
          href={"/login" as Route}
          className="block text-center text-sm text-primary hover:underline"
        >
          {t("backToLogin")}
        </Link>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <div>
        <h2 className="text-base font-semibold text-fg">{t("title")}</h2>
        <p className="mt-1 text-sm text-fg-muted">{t("subtitle")}</p>
      </div>

      {error ? (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-md border border-danger/30 bg-danger-muted/50 px-3 py-2 text-sm text-danger"
        >
          <TriangleAlert className="size-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      <div>
        <Label htmlFor="email">{t("email")}</Label>
        <Input
          id="email"
          name="email"
          type="email"
          autoComplete="email"
          required
          autoFocus
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </div>

      <CaptchaField
        id="captcha"
        label={t("captcha")}
        placeholder={t("captchaPlaceholder")}
        refreshLabel={t("refreshCaptcha")}
        loadingLabel={t("loading")}
        captcha={captcha}
      />

      <Button type="submit" size="lg" className="w-full" disabled={pending || !captcha.ready}>
        <KeyRound className="size-4" />
        {pending ? t("sending") : t("submit")}
      </Button>

      <Link
        href={"/login" as Route}
        className="block text-center text-sm text-primary hover:underline"
      >
        {t("backToLogin")}
      </Link>
    </form>
  );
}
