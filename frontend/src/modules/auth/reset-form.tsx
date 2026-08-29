"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useTranslations } from "next-intl";
import { ShieldCheck, TriangleAlert } from "lucide-react";
import { completePasswordResetAction } from "@/modules/auth/actions";
import { Button } from "@/shared/ui/button";
import { Input, Label } from "@/shared/ui/input";

export function ResetPasswordForm({ token }: { token: string }) {
  const t = useTranslations("resetPassword");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const mismatch = Boolean(confirmPassword) && newPassword !== confirmPassword;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || mismatch) return;
    setError(null);
    setPending(true);
    const result = await completePasswordResetAction({
      token,
      newPassword,
    });
    setPending(false);
    if (!result.ok) {
      setError(result.error || t("errorMessage"));
      return;
    }
    setDone(true);
  }

  if (!token) {
    return (
      <div className="space-y-4">
        <h2 className="text-base font-semibold text-fg">{t("title")}</h2>
        <div
          role="alert"
          className="flex items-center gap-2 rounded-md border border-danger/30 bg-danger-muted/50 px-3 py-2 text-sm text-danger"
        >
          <TriangleAlert className="size-4 shrink-0" />
          <span>{t("invalidToken")}</span>
        </div>
        <Link
          href={"/forgot-password" as Route}
          className="block text-center text-sm text-primary hover:underline"
        >
          {t("requestNewLink")}
        </Link>
      </div>
    );
  }

  if (done) {
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
        <Label htmlFor="new-password">{t("newPassword")}</Label>
        <Input
          id="new-password"
          name="new_password"
          type="password"
          autoComplete="new-password"
          required
          minLength={6}
          autoFocus
          value={newPassword}
          onChange={(event) => setNewPassword(event.target.value)}
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
          value={confirmPassword}
          onChange={(event) => setConfirmPassword(event.target.value)}
        />
        {mismatch ? (
          <p className="mt-1 text-xs text-danger">{t("passwordsMismatch")}</p>
        ) : null}
      </div>

      <Button type="submit" size="lg" className="w-full" disabled={pending || mismatch}>
        <ShieldCheck className="size-4" />
        {pending ? t("resetting") : t("submit")}
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
