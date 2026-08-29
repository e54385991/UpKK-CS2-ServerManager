"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useTranslations } from "next-intl";
import { UserPlus, TriangleAlert } from "lucide-react";
import { registerAccountAction } from "@/modules/auth/actions";
import { Button } from "@/shared/ui/button";
import { CaptchaField, useCaptcha } from "@/shared/ui/captcha-field";
import { Input, Label } from "@/shared/ui/input";

export function RegisterForm() {
  const t = useTranslations("register");
  const captcha = useCaptcha();
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const mismatch = Boolean(confirmPassword) && password !== confirmPassword;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!captcha.ready || mismatch) return;
    setError(null);
    setPending(true);
    const result = await registerAccountAction({
      username: username.trim(),
      email: email.trim(),
      password,
      captchaToken: captcha.token,
      captchaCode: captcha.code.trim(),
    });
    setPending(false);
    captcha.refresh();
    if (!result.ok) {
      setError(result.error || t("errorMessage"));
      return;
    }
    setDone(true);
  }

  if (done) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-fg">{t("successMessage")}</p>
        <Link
          href={"/login" as Route}
          className="block text-center text-sm text-primary hover:underline"
        >
          {t("loginHere")}
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
        <Label htmlFor="username">{t("username")}</Label>
        <Input
          id="username"
          name="username"
          autoComplete="username"
          required
          minLength={3}
          maxLength={100}
          autoFocus
          value={username}
          onChange={(event) => setUsername(event.target.value)}
        />
        <p className="mt-1 text-xs text-fg-subtle">{t("usernameHint")}</p>
      </div>

      <div>
        <Label htmlFor="email">{t("email")}</Label>
        <Input
          id="email"
          name="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </div>

      <div>
        <Label htmlFor="password">{t("password")}</Label>
        <Input
          id="password"
          name="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={6}
          maxLength={100}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <p className="mt-1 text-xs text-fg-subtle">{t("passwordHint")}</p>
      </div>

      <div>
        <Label htmlFor="confirm-password">{t("confirmPassword")}</Label>
        <Input
          id="confirm-password"
          name="confirm_password"
          type="password"
          autoComplete="new-password"
          required
          minLength={6}
          maxLength={100}
          value={confirmPassword}
          onChange={(event) => setConfirmPassword(event.target.value)}
        />
        {mismatch ? (
          <p className="mt-1 text-xs text-danger">{t("passwordsMismatch")}</p>
        ) : null}
      </div>

      <CaptchaField
        id="captcha"
        label={t("captcha")}
        placeholder={t("captchaPlaceholder")}
        refreshLabel={t("refreshCaptcha")}
        loadingLabel={t("loading")}
        captcha={captcha}
      />

      <Button
        type="submit"
        size="lg"
        className="w-full"
        disabled={pending || !captcha.ready || mismatch}
      >
        <UserPlus className="size-4" />
        {pending ? t("submitting") : t("submit")}
      </Button>

      <p className="text-center text-sm text-fg-muted">
        {t("haveAccount")}{" "}
        <Link href={"/login" as Route} className="text-primary hover:underline">
          {t("loginHere")}
        </Link>
      </p>
    </form>
  );
}