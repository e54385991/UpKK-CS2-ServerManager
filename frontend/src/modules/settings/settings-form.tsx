"use client";

import { GitHubTokenCheck } from "@/modules/settings/github-token-check";
import {
AuditRetentionCard,
ClientIpCard,
GoogleLoginCard,
LoggingCard
} from "@/modules/settings/runtime-cards";
import { Field,GmailSetupGuide } from "@/modules/settings/settings-fields";
import { SettingsSection } from "@/modules/settings/settings-section";
import {
type EmailProvider,
type ProxyMode,
type SystemSettings
} from "@/modules/settings/types";
import { cn } from "@/shared/lib/cn";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import {
Card,
CardContent,
CardDescription,
CardHeader,
CardTitle,
} from "@/shared/ui/card";
import { Input,Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";
import { Switch } from "@/shared/ui/switch";
import { Textarea } from "@/shared/ui/textarea";
import {
CloudDownload,
KeyRound,
Mail,
Save,
Send,
ShieldCheck,
TriangleAlert,
Upload,
UserPlus,
} from "lucide-react";

import { type SettingsSectionKey } from "./settings-form-parts";
import { useSettingsForm } from "./use-settings-form";

export function SettingsForm({
  initial,
  activeSection = "downloads",
  onDirty,
  onSaved,
}: {
  initial: SystemSettings;
  activeSection?: SettingsSectionKey;
  onDirty?: () => void;
  onSaved?: () => void;
}) {
  const {
    t,
    settings,
    proxyMode,
    setProxyMode,
    githubProxyUrl,
    setGithubProxyUrl,
    captchaEnabled,
    setCaptchaEnabled,
    registrationEnabled,
    setRegistrationEnabled,
    googleClientId,
    setGoogleClientId,
    clientIpChoice,
    setClientIpChoice,
    clientIpCustom,
    setClientIpCustom,
    logLevel,
    setLogLevel,
    auditRetentionDays,
    setAuditRetentionDays,
    githubToken,
    setGithubToken,
    clearGithubToken,
    setClearGithubToken,
    emailEnabled,
    setEmailEnabled,
    emailProvider,
    setEmailProvider,
    fromAddress,
    setFromAddress,
    fromName,
    setFromName,
    smtpHost,
    setSmtpHost,
    smtpPort,
    setSmtpPort,
    smtpUsername,
    setSmtpUsername,
    smtpPassword,
    setSmtpPassword,
    smtpUseTls,
    setSmtpUseTls,
    gmailJson,
    setGmailJson,
    testEmail,
    setTestEmail,
    saving,
    testing,
    gmailBusy,
    banner,
    onSave,
    onTestEmail,
    onUploadGmail,
    onAuthorizeGmail,
    onRevokeGmail,
  } = useSettingsForm({ initial, activeSection, onDirty, onSaved });

  return (
    <form onSubmit={onSave} onChange={onDirty} noValidate className="space-y-6">
      {banner ? (
        <div
          role="status"
          className={cn(
            "flex items-start gap-2 rounded-md border px-4 py-3 text-sm",
            banner.tone === "ok" &&
              "border-ok/30 bg-ok-muted/40 text-ok",
            banner.tone === "warn" &&
              "border-warn/30 bg-warn-muted/40 text-warn",
            banner.tone === "danger" &&
              "border-danger/30 bg-danger-muted/40 text-danger",
          )}
        >
          {banner.tone === "ok" ? (
            <ShieldCheck className="mt-0.5 size-4 shrink-0" />
          ) : (
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          )}
          <span>{banner.text}</span>
        </div>
      ) : null}

      <div
        className={cn(
          "sticky top-4 z-10 flex items-center justify-between gap-3 border border-line bg-surface/95 px-3 py-2 backdrop-blur",
          !["downloads", "notifications", "security", "logging"].includes(activeSection) &&
            "hidden",
        )}
      >
        <span className="text-xs text-fg-subtle">
          {activeSection === "hidden" ? "" : t(`categories.${activeSection}`)}
        </span>
        <Button type="submit" disabled={saving}>
          <Save className="size-4" />
          {saving ? t("saving") : t("save")}
        </Button>
      </div>

      <SettingsSection
        className={activeSection === "downloads" ? undefined : "hidden"}
        id="settings-downloads"
        title={t("sections.downloads.title")}
        description={t("sections.downloads.description")}
        testId="settings-section-downloads"
      >
        <Card>
          <CardHeader>
            <div className="flex items-center gap-3">
              <span className="flex size-9 items-center justify-center rounded-md bg-primary-muted text-primary ring-1 ring-primary/30">
                <CloudDownload className="size-4" />
              </span>
              <div>
                <CardTitle>{t("proxy.title")}</CardTitle>
                <CardDescription>{t("proxy.description")}</CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <Field label={t("proxy.mode")} htmlFor="proxy-mode" hint={t("proxy.modeHelp")}>
              <Select
                id="proxy-mode"
                value={proxyMode}
                onChange={(event) =>
                  setProxyMode(event.target.value as ProxyMode)
                }
              >
                <option value="direct">{t("proxy.direct")}</option>
                <option value="panel">{t("proxy.panel")}</option>
                <option value="github_url">{t("proxy.githubUrl")}</option>
              </Select>
            </Field>

            {proxyMode === "github_url" ? (
              <Field
                label={t("proxy.githubProxyUrl")}
                htmlFor="github-proxy-url"
                hint={t("proxy.githubProxyUrlHelp")}
              >
                <Input
                  id="github-proxy-url"
                  type="url"
                  value={githubProxyUrl}
                  onChange={(event) => setGithubProxyUrl(event.target.value)}
                  placeholder="https://ghfast.top"
                />
              </Field>
            ) : null}

            <div className="rounded-md border border-line bg-surface-raised/40 px-4 py-3">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-sm font-medium text-fg">
                  <KeyRound className="size-4 text-fg-subtle" />
                  {t("token.label")}
                </div>
                <Badge tone={settings.hasGlobalGithubToken ? "ok" : "neutral"}>
                  {settings.hasGlobalGithubToken
                    ? settings.globalGithubTokenPrefix ?? t("token.configured")
                    : t("token.notConfigured")}
                </Badge>
              </div>
              <Field hint={t("token.help")}>
                <Input
                  id="global-github-token"
                  type="password"
                  autoComplete="new-password"
                  value={githubToken}
                  disabled={clearGithubToken}
                  onChange={(event) => setGithubToken(event.target.value)}
                  placeholder={t("token.placeholder")}
                />
              </Field>
              <GitHubTokenCheck initial={settings.githubTokenVerification} key={`${settings.updatedAt ?? ""}:${Boolean(githubToken)}:${clearGithubToken}`} disabled={!settings.hasGlobalGithubToken || Boolean(githubToken) || clearGithubToken || saving} />
              {settings.hasGlobalGithubToken ? (
                <label className="mt-3 flex items-center gap-2 text-sm text-fg-muted">
                  <input
                    type="checkbox"
                    className="size-4 rounded border-line accent-primary"
                    checked={clearGithubToken}
                    onChange={(event) => {
                      setClearGithubToken(event.target.checked);
                      if (event.target.checked) setGithubToken("");
                    }}
                  />
                  {t("token.clear")}
                </label>
              ) : null}
            </div>
          </CardContent>
        </Card>
      </SettingsSection>

      <SettingsSection
        className={activeSection === "notifications" ? undefined : "hidden"}
        id="settings-notifications"
        title={t("sections.notifications.title")}
        description={t("sections.notifications.description")}
        testId="settings-section-notifications"
      >
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <span className="flex size-9 items-center justify-center rounded-md bg-info-muted text-info ring-1 ring-info/30">
                  <Mail className="size-4" />
                </span>
                <div>
                  <CardTitle>{t("email.title")}</CardTitle>
                  <CardDescription>{t("email.description")}</CardDescription>
                </div>
              </div>
              <Switch
                id="email-enabled"
                label={t("email.enabled")}
                checked={emailEnabled}
                onCheckedChange={(next) => { setEmailEnabled(next); onDirty?.(); }}
              />
            </div>
          </CardHeader>
          <CardContent className={cn("space-y-4", !emailEnabled && "opacity-60")}>
            <Field label={t("email.provider")} htmlFor="email-provider">
              <Select
                id="email-provider"
                value={emailProvider}
                disabled={!emailEnabled}
                onChange={(event) =>
                  setEmailProvider(event.target.value as EmailProvider)
                }
              >
                <option value="smtp">{t("email.smtp")}</option>
                <option value="gmail">{t("email.gmail")}</option>
              </Select>
            </Field>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                label={t("email.fromAddress")}
                htmlFor="from-address"
                hint={t("email.fromAddressHelp")}
              >
                <Input
                  id="from-address"
                  type="email"
                  disabled={!emailEnabled}
                  value={fromAddress}
                  onChange={(event) => setFromAddress(event.target.value)}
                  placeholder="noreply@example.com"
                />
              </Field>
              <Field
                label={t("email.fromName")}
                htmlFor="from-name"
                hint={t("email.fromNameHelp")}
              >
                <Input
                  id="from-name"
                  disabled={!emailEnabled}
                  value={fromName}
                  onChange={(event) => setFromName(event.target.value)}
                  placeholder="CS2 Server Manager"
                />
              </Field>
            </div>

            {emailProvider === "smtp" ? (
              <div className="space-y-4 rounded-md border border-line px-4 py-3">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium text-fg">{t("smtp.title")}</p>
                  <Badge tone={settings.hasSmtpPassword ? "ok" : "neutral"}>
                    {settings.hasSmtpPassword
                      ? t("smtp.passwordSet")
                      : t("smtp.passwordMissing")}
                  </Badge>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label={t("smtp.host")} htmlFor="smtp-host">
                    <Input
                      id="smtp-host"
                      disabled={!emailEnabled}
                      value={smtpHost}
                      onChange={(event) => setSmtpHost(event.target.value)}
                    />
                  </Field>
                  <Field label={t("smtp.port")} htmlFor="smtp-port">
                    <Input
                      id="smtp-port"
                      type="number"
                      min={1}
                      max={65535}
                      disabled={!emailEnabled}
                      value={smtpPort}
                      onChange={(event) => setSmtpPort(event.target.value)}
                    />
                  </Field>
                  <Field label={t("smtp.username")} htmlFor="smtp-username">
                    <Input
                      id="smtp-username"
                      disabled={!emailEnabled}
                      value={smtpUsername}
                      onChange={(event) => setSmtpUsername(event.target.value)}
                    />
                  </Field>
                  <Field
                    label={t("smtp.password")}
                    htmlFor="smtp-password"
                    hint={t("smtp.passwordHelp")}
                  >
                    <Input
                      id="smtp-password"
                      type="password"
                      autoComplete="new-password"
                      disabled={!emailEnabled}
                      value={smtpPassword}
                      onChange={(event) => setSmtpPassword(event.target.value)}
                      placeholder="••••••••"
                    />
                  </Field>
                </div>
                <div className="flex items-center justify-between">
                  <Label htmlFor="smtp-tls" className="mb-0">
                    {t("smtp.useTls")}
                  </Label>
                  <Switch
                    id="smtp-tls"
                    label={t("smtp.useTls")}
                    checked={smtpUseTls}
                    disabled={!emailEnabled}
                    onCheckedChange={(next) => { setSmtpUseTls(next); onDirty?.(); }}
                  />
                </div>
              </div>
            ) : (
              <div className="space-y-4 rounded-md border border-line px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm font-medium text-fg">{t("gmail.title")}</p>
                  <Badge
                    tone={
                      settings.gmailReady
                        ? "ok"
                        : settings.hasGmailCredentials
                          ? "warn"
                          : "neutral"
                    }
                  >
                    {settings.gmailReady
                      ? t("gmail.ready")
                      : settings.hasGmailCredentials
                        ? t("gmail.needsAuth")
                        : t("gmail.needsCredentials")}
                  </Badge>
                </div>
                <p className="text-xs text-fg-muted">{t("gmail.info")}</p>
                <GmailSetupGuide />
                <Field
                  label={t("gmail.credentials")}
                  htmlFor="gmail-json"
                  hint={t("gmail.credentialsHelp")}
                >
                  <Textarea
                    id="gmail-json"
                    disabled={!emailEnabled}
                    value={gmailJson}
                    onChange={(event) => setGmailJson(event.target.value)}
                    placeholder='{"web":{"client_id":"..."}}'
                    spellCheck={false}
                  />
                </Field>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={!emailEnabled || gmailBusy !== null}
                    onClick={() => void onUploadGmail()}
                  >
                    <Upload className="size-4" />
                    {gmailBusy === "upload"
                      ? t("gmail.uploading")
                      : t("gmail.upload")}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={
                      !emailEnabled ||
                      !settings.hasGmailCredentials ||
                      gmailBusy !== null
                    }
                    onClick={() => void onAuthorizeGmail()}
                  >
                    {gmailBusy === "auth"
                      ? t("gmail.authorizing")
                      : t("gmail.authorize")}
                  </Button>
                  {settings.hasGmailToken ? (
                    <Button
                      type="button"
                      variant="danger"
                      disabled={!emailEnabled || gmailBusy !== null}
                      onClick={() => void onRevokeGmail()}
                    >
                      {gmailBusy === "revoke"
                        ? t("gmail.revoking")
                        : t("gmail.revoke")}
                    </Button>
                  ) : null}
                </div>
              </div>
            )}

            <div className="space-y-2 border-t border-line pt-4">
              <Label htmlFor="test-email">{t("test.label")}</Label>
              <div className="flex flex-col gap-2 sm:flex-row">
                <Input
                  id="test-email"
                  type="email"
                  disabled={!emailEnabled}
                  value={testEmail}
                  onChange={(event) => setTestEmail(event.target.value)}
                  placeholder={t("test.placeholder")}
                />
                <Button
                  type="button"
                  variant="secondary"
                  disabled={!emailEnabled || testing}
                  onClick={() => void onTestEmail()}
                >
                  <Send className="size-4" />
                  {testing ? t("test.sending") : t("test.send")}
                </Button>
              </div>
              <p className="text-xs text-fg-subtle">{t("test.help")}</p>
            </div>
          </CardContent>
        </Card>
      </SettingsSection>

      <SettingsSection
        className={activeSection === "security" ? undefined : "hidden"}
        id="settings-security"
        title={t("sections.security.title")}
        description={t("sections.security.description")}
        testId="settings-section-security"
      >
        <ClientIpCard
          settings={settings}
          choice={clientIpChoice}
          onChoiceChange={setClientIpChoice}
          custom={clientIpCustom}
          onCustomChange={setClientIpCustom}
        />

        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <span className="flex size-9 items-center justify-center rounded-md bg-warn-muted text-warn ring-1 ring-warn/30">
                  <ShieldCheck className="size-4" />
                </span>
                <div>
                  <CardTitle>{t("captcha.title")}</CardTitle>
                  <CardDescription>{t("captcha.description")}</CardDescription>
                </div>
              </div>
              <Switch
                id="captcha-enabled"
                label={t("captcha.enabled")}
                checked={captchaEnabled}
                onCheckedChange={(next) => { setCaptchaEnabled(next); onDirty?.(); }}
              />
            </div>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-fg-muted">
              {captchaEnabled ? t("captcha.enabledHelp") : t("captcha.disabledHelp")}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <span className="flex size-9 items-center justify-center rounded-md bg-primary-muted text-primary ring-1 ring-primary/30">
                  <UserPlus className="size-4" />
                </span>
                <div>
                  <CardTitle>{t("registration.title")}</CardTitle>
                  <CardDescription>{t("registration.description")}</CardDescription>
                </div>
              </div>
              <Switch
                id="registration-enabled"
                label={t("registration.enabled")}
                checked={registrationEnabled}
                onCheckedChange={(next) => { setRegistrationEnabled(next); onDirty?.(); }}
              />
            </div>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-fg-muted">
              {registrationEnabled
                ? t("registration.enabledHelp")
                : t("registration.disabledHelp")}
            </p>
          </CardContent>
        </Card>

        <GoogleLoginCard
          clientId={googleClientId}
          onClientIdChange={(value) => {
            setGoogleClientId(value);
            onDirty?.();
          }}
          fromEnvironment={settings.googleLoginFromEnvironment}
          enabled={Boolean(googleClientId.trim()) || settings.googleLoginEnabled}
          effectiveClientId={settings.effectiveGoogleClientId}
        />
      </SettingsSection>

      <SettingsSection
        className={activeSection === "logging" ? undefined : "hidden"}
        id="settings-logging"
        title={t("sections.logging.title")}
        description={t("sections.logging.description")}
        testId="settings-section-logging"
      >
        <LoggingCard
          settings={settings}
          level={logLevel}
          onLevelChange={setLogLevel}
        />
        <AuditRetentionCard
          days={auditRetentionDays}
          onDaysChange={setAuditRetentionDays}
        />
      </SettingsSection>

    </form>
  );
}

export type { SettingsSectionKey } from "./settings-form-parts";
