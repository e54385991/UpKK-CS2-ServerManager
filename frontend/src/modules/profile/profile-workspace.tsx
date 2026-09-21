"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { ApiKeyForm } from "@/modules/profile/api-key-form";
import { PasskeyForm } from "@/modules/profile/passkey-form";
import { PasswordForm } from "@/modules/profile/password-form";
import { ProfileCredentialsForm } from "@/modules/profile/profile-credentials-form";
import {
  PROFILE_SECTIONS,
  profileSectionFromHash,
  type ProfileSectionKey,
} from "@/modules/profile/profile-nav";
import { S3Form } from "@/modules/profile/s3-form";
import { SteamcmdRetryForm } from "@/modules/profile/steamcmd-retry-form";
import type {
  PasskeyItem,
  ProfileAiSettings,
  ProfileS3Settings,
  ProfileSettings,
} from "@/modules/profile/types";
import { UserAiForm } from "@/modules/profile/user-ai-form";
import { SettingsSection } from "@/modules/settings/settings-section";
import { cn } from "@/shared/lib/cn";
import { Badge } from "@/shared/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/ui/card";

export function ProfileWorkspace({
  profile,
  joined,
  roleLabel,
  s3,
  ai,
  passkeys,
  passkeysError,
}: {
  profile: ProfileSettings;
  joined: string;
  roleLabel: string;
  s3: ProfileS3Settings | null;
  ai: ProfileAiSettings | null;
  passkeys: readonly PasskeyItem[];
  passkeysError: boolean;
}) {
  const t = useTranslations("profile");
  const [active, setActive] = useState<ProfileSectionKey>("account");

  useEffect(() => {
    const applyHash = () => setActive(profileSectionFromHash(window.location.hash));
    if (!window.location.hash) {
      window.history.replaceState(
        null,
        "",
        `${window.location.pathname}${window.location.search}#profile-account`,
      );
    }
    applyHash();
    window.addEventListener("hashchange", applyHash);
    return () => window.removeEventListener("hashchange", applyHash);
  }, []);

  useEffect(() => {
    if (active !== "credentials") return;
    const fieldId = window.location.hash.slice(1);
    if (fieldId === "profile-github-token" || fieldId === "profile-steam-key") {
      document.getElementById(fieldId)?.focus();
    }
  }, [active]);

  function select(section: (typeof PROFILE_SECTIONS)[number]) {
    setActive(section.key);
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${window.location.search}#${section.id}`,
    );
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[14rem_minmax(0,1fr)]">
      <nav
        aria-label={t("categoryNavLabel")}
        data-testid="profile-category-nav"
        className="h-fit rounded-lg border border-line bg-surface p-2 shadow-panel lg:sticky lg:top-4"
      >
        <div className="flex gap-1 overflow-x-auto lg:block lg:space-y-1">
          {PROFILE_SECTIONS.map((section) => {
            const selected = active === section.key;
            return (
              <a
                key={section.id}
                href={`#${section.id}`}
                aria-current={selected ? "page" : undefined}
                data-active={selected ? "true" : undefined}
                onClick={() => select(section)}
                className={cn(
                  "flex min-w-max items-center rounded-md px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
                  selected
                    ? "bg-primary-muted text-primary"
                    : "text-fg-muted hover:bg-surface-raised hover:text-fg",
                )}
              >
                {t(`categories.${section.key}`)}
              </a>
            );
          })}
        </div>
      </nav>

      <div className="min-w-0">
        <div className={cn(active === "account" ? "" : "hidden")}>
          <SettingsSection
            id="profile-account"
            title={t("sections.account.title")}
            description={t("sections.account.description")}
            testId="profile-section-account"
          >
            <Card className="max-w-2xl">
              <CardHeader>
                <CardTitle>{t("account")}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <Row label={t("username")} value={profile.username} />
                <Row label={t("email")} value={profile.email ?? "—"} />
                <div className="flex items-center justify-between">
                  <span className="text-sm text-fg-muted">{t("role")}</span>
                  <Badge tone={profile.isAdmin ? "primary" : "neutral"}>{roleLabel}</Badge>
                </div>
                <Row label={t("joinedAt")} value={joined} />
              </CardContent>
            </Card>
          </SettingsSection>
        </div>

        <div className={cn(active === "credentials" ? "" : "hidden")}>
          <SettingsSection
            id="profile-credentials"
            title={t("sections.credentials.title")}
            description={t("sections.credentials.description")}
            testId="profile-section-credentials"
          >
            <ProfileCredentialsForm initial={profile} />
          </SettingsSection>
        </div>

        <div className={cn(active === "security" ? "" : "hidden")}>
          <SettingsSection
            id="profile-security"
            title={t("sections.security.title")}
            description={t("sections.security.description")}
            testId="profile-section-security"
          >
            <div className="space-y-6">
              <PasswordForm />
              <PasskeyForm initial={passkeys} loadError={passkeysError} />
              <ApiKeyForm initial={profile} />
            </div>
          </SettingsSection>
        </div>

        <div className={cn(active === "backups" ? "" : "hidden")}>
          <SettingsSection
            id="profile-backups"
            title={t("sections.backups.title")}
            description={t("sections.backups.description")}
            testId="profile-section-backups"
          >
            {s3 ? (
              <S3Form initial={s3} />
            ) : (
              <Card className="max-w-2xl border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
                {t("s3LoadError")}
              </Card>
            )}
          </SettingsSection>
        </div>

        <div className={cn(active === "ai" ? "" : "hidden")}>
          <SettingsSection
            id="profile-ai"
            title={t("sections.ai.title")}
            description={t("sections.ai.description")}
            testId="profile-section-ai"
          >
            <UserAiForm initial={ai} />
          </SettingsSection>
        </div>

        <div className={cn(active === "operations" ? "" : "hidden")}>
          <SettingsSection
            id="profile-operations"
            title={t("sections.operations.title")}
            description={t("sections.operations.description")}
            testId="profile-section-operations"
          >
            <SteamcmdRetryForm initial={profile} />
          </SettingsSection>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-sm text-fg-muted">{label}</span>
      <span className="text-sm font-medium text-fg">{value}</span>
    </div>
  );
}
