"use client";

import { GitHubTokenCheck } from "@/modules/settings/github-token-check";
import { useState, type FormEvent } from "react";
import { useTranslations } from "next-intl";
import {
  CloudDownload,
  KeyRound,
  Mail,
  Save,
  Send,
  ShieldCheck,
  TriangleAlert,
  UserPlus,
  Upload,
} from "lucide-react";
import {
  authorizeGmailAction,
  refreshSettingsAction,
  revokeGmailAction,
  saveSettingsAction,
  sendTestEmailAction,
  uploadGmailCredentialsAction,
} from "@/modules/settings/actions";
import {
  isClientIpHeader,
  isGoogleClientId,
  type EmailProvider,
  type ProxyMode,
  type SystemSettings,
} from "@/modules/settings/types";
import {
  ClientIpCard,
  ENVIRONMENT_LOG_LEVEL,
  LoggingCard,
  AuditRetentionCard,
  GoogleLoginCard,
  AUDIT_LOG_RETENTION_DAYS_MAX,
  AUDIT_LOG_RETENTION_DAYS_MIN,
  clientIpChoiceOf,
  clientIpHeaderOf,
  customClientIpOf,
  logLevelOf,
} from "@/modules/settings/runtime-cards";
import { SettingsSection } from "@/modules/settings/settings-section";
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
import { Input, Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";
import { Switch } from "@/shared/ui/switch";
import { Textarea } from "@/shared/ui/textarea";
import { cn } from "@/shared/lib/cn";
import { Field, GmailSetupGuide } from "@/modules/settings/settings-fields";

type Banner = { readonly tone: "ok" | "warn" | "danger"; readonly text: string };

export type SettingsSectionKey =
  | "downloads"
  | "notifications"
  | "security"
  | "logging"
  | "hidden";

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
  const t = useTranslations("settings");
  const [settings, setSettings] = useState(initial);
  const [proxyMode, setProxyMode] = useState<ProxyMode>(initial.defaultProxyMode);
  const [githubProxyUrl, setGithubProxyUrl] = useState(
    initial.githubProxyUrl ?? "",
  );
  const [captchaEnabled, setCaptchaEnabled] = useState(initial.captchaEnabled);
  const [registrationEnabled, setRegistrationEnabled] = useState(
    initial.registrationEnabled,
  );
  const [googleClientId, setGoogleClientId] = useState(
    initial.googleClientId ?? "",
  );
  const [clientIpChoice, setClientIpChoice] = useState(
    clientIpChoiceOf(initial.clientIpHeader),
  );
  const [clientIpCustom, setClientIpCustom] = useState(
    customClientIpOf(initial.clientIpHeader),
  );
  const [logLevel, setLogLevel] = useState<string>(
    initial.logLevel ?? ENVIRONMENT_LOG_LEVEL,
  );
  const [auditRetentionDays, setAuditRetentionDays] = useState(
    String(initial.auditLogRetentionDays),
  );
  const [githubToken, setGithubToken] = useState("");
  const [clearGithubToken, setClearGithubToken] = useState(false);
  const [emailEnabled, setEmailEnabled] = useState(initial.emailEnabled);
  const [emailProvider, setEmailProvider] = useState<EmailProvider>(
    initial.emailProvider,
  );
  const [fromAddress, setFromAddress] = useState(initial.emailFromAddress ?? "");
  const [fromName, setFromName] = useState(initial.emailFromName ?? "");
  const [smtpHost, setSmtpHost] = useState(initial.smtpHost ?? "");
  const [smtpPort, setSmtpPort] = useState(String(initial.smtpPort ?? 587));
  const [smtpUsername, setSmtpUsername] = useState(initial.smtpUsername ?? "");
  const [smtpPassword, setSmtpPassword] = useState("");
  const [smtpUseTls, setSmtpUseTls] = useState(initial.smtpUseTls);
  const [gmailJson, setGmailJson] = useState("");
  const [testEmail, setTestEmail] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [gmailBusy, setGmailBusy] = useState<"upload" | "auth" | "revoke" | null>(
    null,
  );
  const [banner, setBanner] = useState<Banner | null>(null);

  async function onSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const clientIpHeader = clientIpHeaderOf(clientIpChoice, clientIpCustom);
    if (
      activeSection === "security" &&
      clientIpHeader !== null &&
      !isClientIpHeader(clientIpHeader)
    ) {
      setBanner({ tone: "warn", text: t("clientIp.invalid") });
      return;
    }
    if (activeSection === "security" && !isGoogleClientId(googleClientId)) {
      setBanner({ tone: "warn", text: t("googleLogin.invalid") });
      return;
    }
    const parsedRetention = Number(auditRetentionDays);
    if (
      activeSection === "logging" &&
      (!Number.isInteger(parsedRetention) ||
        parsedRetention < AUDIT_LOG_RETENTION_DAYS_MIN ||
        parsedRetention > AUDIT_LOG_RETENTION_DAYS_MAX)
    ) {
      setBanner({ tone: "warn", text: t("auditRetention.invalid") });
      return;
    }
    setSaving(true);
    setBanner(null);
    const parsedPort = Number(smtpPort);
    const patch =
      activeSection === "downloads"
        ? {
            defaultProxyMode: proxyMode,
            githubProxyUrl: githubProxyUrl.trim() || null,
            ...(clearGithubToken
              ? { clearGlobalGithubToken: true }
              : githubToken.trim()
                ? { globalGithubToken: githubToken.trim() }
                : {}),
          }
        : activeSection === "notifications"
          ? {
              emailEnabled,
              emailProvider,
              emailFromAddress: fromAddress.trim() || null,
              emailFromName: fromName.trim() || null,
              smtpHost: smtpHost.trim() || null,
              smtpPort: Number.isInteger(parsedPort) ? parsedPort : 587,
              smtpUsername: smtpUsername.trim() || null,
              ...(smtpPassword.trim() ? { smtpPassword: smtpPassword.trim() } : {}),
              smtpUseTls,
            }
          : activeSection === "security"
            ? {
                captchaEnabled,
                registrationEnabled,
                clientIpHeader,
                googleClientId: googleClientId.trim() || null,
              }
            : activeSection === "logging"
            ? {
                logLevel: logLevelOf(logLevel),
                auditLogRetentionDays: parsedRetention,
              }
            : {};
    const result = await saveSettingsAction(patch);
    setSaving(false);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error || t("saveFailed") });
      return;
    }
    setSettings(result.data);
    if (activeSection === "security") {
      setCaptchaEnabled(result.data.captchaEnabled);
      setRegistrationEnabled(result.data.registrationEnabled);
      setClientIpChoice(clientIpChoiceOf(result.data.clientIpHeader));
      setClientIpCustom(customClientIpOf(result.data.clientIpHeader));
      setGoogleClientId(result.data.googleClientId ?? "");
    }
    if (activeSection === "logging") {
      setLogLevel(result.data.logLevel ?? ENVIRONMENT_LOG_LEVEL);
      setAuditRetentionDays(String(result.data.auditLogRetentionDays));
    }
    if (activeSection === "downloads") { setGithubToken(""); setClearGithubToken(false); }
    if (activeSection === "notifications") setSmtpPassword("");
    onSaved?.();
    setBanner({ tone: "ok", text: t("saved") });
  }

  async function onTestEmail() {
    if (!testEmail.trim()) {
      setBanner({ tone: "warn", text: t("test.required") });
      return;
    }
    setTesting(true);
    setBanner(null);
    const result = await sendTestEmailAction(testEmail.trim());
    setTesting(false);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error || t("test.failed") });
      return;
    }
    setBanner({ tone: "ok", text: result.data.message });
  }

  async function onUploadGmail() {
    if (!gmailJson.trim()) {
      setBanner({ tone: "warn", text: t("gmail.credentialsRequired") });
      return;
    }
    setGmailBusy("upload");
    setBanner(null);
    const result = await uploadGmailCredentialsAction(gmailJson.trim());
    setGmailBusy(null);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error });
      return;
    }
    setGmailJson("");
    setBanner({ tone: "ok", text: result.data.message });
    const refreshed = await refreshSettingsAction();
    if (refreshed.ok) setSettings(refreshed.data);
  }

  async function onAuthorizeGmail() {
    setGmailBusy("auth");
    setBanner(null);
    const result = await authorizeGmailAction();
    setGmailBusy(null);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error });
      return;
    }
    const popup = window.open(
      result.data.authorizationUrl,
      "gmail-oauth",
      "width=600,height=720",
    );
    if (!popup) {
      setBanner({ tone: "warn", text: t("gmail.popupBlocked") });
      return;
    }
    setBanner({ tone: "ok", text: t("gmail.waiting") });
    const started = Date.now();
    const timer = window.setInterval(() => {
      void refreshSettingsAction().then((next) => {
        if (next.ok) {
          setSettings(next.data);
          if (next.data.gmailReady) {
            window.clearInterval(timer);
            setBanner({ tone: "ok", text: t("gmail.authorized") });
          }
        }
        if (Date.now() - started > 5 * 60 * 1000) {
          window.clearInterval(timer);
        }
      });
    }, 3000);
  }

  async function onRevokeGmail() {
    if (!(await confirm(t("gmail.revokeConfirm")))) return;
    setGmailBusy("revoke");
    setBanner(null);
    const result = await revokeGmailAction();
    setGmailBusy(null);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error });
      return;
    }
    setBanner({ tone: "ok", text: result.data.message });
    const refreshed = await refreshSettingsAction();
    if (refreshed.ok) setSettings(refreshed.data);
  }

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
