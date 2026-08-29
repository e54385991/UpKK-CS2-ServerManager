"use client";

import { useState, type FormEvent } from "react";
import { useTranslations } from "next-intl";
import { changePasswordAction } from "@/modules/profile/actions";
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

export function PasswordForm() {
  const t = useTranslations("profile");
  const captcha = useCaptcha();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);

  async function onSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!captcha.ready) return;
    if (newPassword !== confirmPassword) {
      setBanner(t("passwordMismatch"));
      return;
    }
    setSaving(true);
    setBanner(null);
    const result = await changePasswordAction({
      currentPassword,
      newPassword,
      confirmPassword,
      captchaToken: captcha.token,
      captchaCode: captcha.code.trim(),
    });
    setSaving(false);
    captcha.refresh();
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setCurrentPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setBanner(result.data.message || t("passwordSaved"));
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <div className="space-y-1">
          <CardTitle>{t("passwordTitle")}</CardTitle>
          <CardDescription>{t("passwordHelp")}</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSave} className="space-y-4">
          <div>
            <Label htmlFor="current-password">{t("currentPassword")}</Label>
            <Input
              id="current-password"
              type="password"
              autoComplete="current-password"
              required
              minLength={6}
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="new-password">{t("newPassword")}</Label>
            <Input
              id="new-password"
              type="password"
              autoComplete="new-password"
              required
              minLength={6}
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
            />
            <p className="mt-1.5 text-xs text-fg-subtle">{t("passwordHint")}</p>
          </div>
          <div>
            <Label htmlFor="confirm-password">{t("confirmPassword")}</Label>
            <Input
              id="confirm-password"
              type="password"
              autoComplete="new-password"
              required
              minLength={6}
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
            />
          </div>
          <CaptchaField
            id="password-captcha"
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
          <Button type="submit" disabled={saving || !captcha.ready}>
            {saving ? t("saving") : t("resetPasswordButton")}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
