"use client";

import { useState, type FormEvent } from "react";
import { useTranslations } from "next-intl";
import { saveProfileCredentialsAction } from "@/modules/profile/actions";
import type { ProfileSettings } from "@/modules/profile/types";
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

export function ProfileCredentialsForm({ initial }: { initial: ProfileSettings }) {
  const t = useTranslations("profile");
  const captcha = useCaptcha();
  const [email, setEmail] = useState(initial.email ?? "");
  const [steamKey, setSteamKey] = useState("");
  const [clearSteam, setClearSteam] = useState(false);
  const [githubToken, setGithubToken] = useState("");
  const [clearGithub, setClearGithub] = useState(false);
  const [profile, setProfile] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);

  async function onSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!captcha.ready) return;
    setSaving(true);
    setBanner(null);
    const result = await saveProfileCredentialsAction({
      email: email.trim() || null,
      ...(clearSteam
        ? { clearSteamApiKey: true }
        : steamKey.trim()
          ? { steamApiKey: steamKey.trim() }
          : {}),
      ...(clearGithub
        ? { clearGithubToken: true }
        : githubToken.trim()
          ? { githubToken: githubToken.trim() }
          : {}),
      captchaToken: captcha.token,
      captchaCode: captcha.code.trim(),
    });
    setSaving(false);
    captcha.refresh();
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setProfile(result.data);
    setEmail(result.data.email ?? "");
    setSteamKey("");
    setGithubToken("");
    setClearSteam(false);
    setClearGithub(false);
    setBanner(t("saved"));
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <div className="space-y-1">
          <CardTitle>{t("updateTitle")}</CardTitle>
          <CardDescription>{t("updateHelp")}</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSave} className="space-y-4">
          <div>
            <Label htmlFor="profile-email">{t("newEmail")}</Label>
            <Input
              id="profile-email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder={t("emailPlaceholder")}
            />
            <p className="mt-1.5 text-xs text-fg-subtle">{t("emailHint")}</p>
          </div>

          <div>
            <div className="mb-1.5 flex items-center justify-between gap-3">
              <Label htmlFor="profile-steam-key" className="mb-0">
                {t("steamKey")}
              </Label>
              <Badge tone={profile.hasSteamApiKey ? "ok" : "neutral"}>
                {profile.hasSteamApiKey
                  ? profile.steamApiKeyPrefix ?? t("configured")
                  : t("notConfigured")}
              </Badge>
            </div>
            <Input
              id="profile-steam-key"
              type="password"
              autoComplete="off"
              disabled={clearSteam}
              value={steamKey}
              onChange={(event) => setSteamKey(event.target.value)}
              placeholder={t("steamKeyPlaceholder")}
              maxLength={32}
            />
            <p className="mt-1.5 text-xs text-fg-subtle">
              {t("steamKeyHelp")}{" "}
              <a
                href="https://steamcommunity.com/dev/apikey"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary underline-offset-2 hover:underline"
              >
                {t("steamKeyLink")}
              </a>
            </p>
            {profile.hasSteamApiKey ? (
              <label className="mt-2 flex items-center gap-2 text-sm text-fg-muted">
                <input
                  type="checkbox"
                  className="size-4 rounded border-line accent-primary"
                  checked={clearSteam}
                  onChange={(event) => {
                    setClearSteam(event.target.checked);
                    if (event.target.checked) setSteamKey("");
                  }}
                />
                {t("clearSteamKey")}
              </label>
            ) : null}
          </div>

          <div>
            <div className="mb-1.5 flex items-center justify-between gap-3">
              <Label htmlFor="profile-github-token" className="mb-0">
                {t("githubToken")}
              </Label>
              <Badge tone={profile.hasGithubToken ? "ok" : "neutral"}>
                {profile.hasGithubToken
                  ? profile.githubTokenPrefix ?? t("configured")
                  : t("notConfigured")}
              </Badge>
            </div>
            <Input
              id="profile-github-token"
              type="password"
              autoComplete="new-password"
              disabled={clearGithub}
              value={githubToken}
              onChange={(event) => setGithubToken(event.target.value)}
              placeholder={t("githubTokenPlaceholder")}
            />
            <p className="mt-1.5 text-xs text-fg-subtle">
              {t("githubTokenHelp")}{" "}
              <a
                href="https://github.com/settings/tokens?type=beta"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary underline-offset-2 hover:underline"
              >
                {t("githubTokenLink")}
              </a>
            </p>
            {profile.hasGithubToken ? (
              <label className="mt-2 flex items-center gap-2 text-sm text-fg-muted">
                <input
                  type="checkbox"
                  className="size-4 rounded border-line accent-primary"
                  checked={clearGithub}
                  onChange={(event) => {
                    setClearGithub(event.target.checked);
                    if (event.target.checked) setGithubToken("");
                  }}
                />
                {t("clearGithubToken")}
              </label>
            ) : null}
          </div>

          <CaptchaField
            id="profile-captcha"
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
            {saving ? t("saving") : t("updateButton")}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
