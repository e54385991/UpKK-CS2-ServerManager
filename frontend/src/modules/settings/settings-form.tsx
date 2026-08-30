"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import {
  CloudDownload,
  KeyRound,
  Mail,
  Save,
  Send,
  ShieldCheck,
  TriangleAlert,
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
import type {
  EmailProvider,
  ProxyMode,
  SystemSettings,
} from "@/modules/settings/types";
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

type Banner = { readonly tone: "ok" | "warn" | "danger"; readonly text: string };

export function SettingsForm({ initial }: { initial: SystemSettings }) {
  const t = useTranslations("settings");
  const [settings, setSettings] = useState(initial);
  const [proxyMode, setProxyMode] = useState<ProxyMode>(initial.defaultProxyMode);
  const [githubProxyUrl, setGithubProxyUrl] = useState(
    initial.githubProxyUrl ?? "",
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
    setSaving(true);
    setBanner(null);
    const parsedPort = Number(smtpPort);
    const result = await saveSettingsAction({
      defaultProxyMode: proxyMode,
      githubProxyUrl: githubProxyUrl.trim() || null,
      ...(clearGithubToken
        ? { clearGlobalGithubToken: true }
        : githubToken.trim()
          ? { globalGithubToken: githubToken.trim() }
          : {}),
      emailEnabled,
      emailProvider,
      emailFromAddress: fromAddress.trim() || null,
      emailFromName: fromName.trim() || null,
      smtpHost: smtpHost.trim() || null,
      smtpPort: Number.isInteger(parsedPort) ? parsedPort : 587,
      smtpUsername: smtpUsername.trim() || null,
      ...(smtpPassword.trim() ? { smtpPassword: smtpPassword.trim() } : {}),
      smtpUseTls,
    });
    setSaving(false);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error || t("saveFailed") });
      return;
    }
    setSettings(result.data);
    setGithubToken("");
    setClearGithubToken(false);
    setSmtpPassword("");
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
    <form onSubmit={onSave} className="space-y-6">
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

      <div className="grid gap-6 xl:grid-cols-2">
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
                onCheckedChange={setEmailEnabled}
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
                    onCheckedChange={setSmtpUseTls}
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
      </div>

      <div className="flex items-center justify-between gap-3 border-t border-line pt-4">
        <p className="text-xs text-fg-subtle">
          {settings.updatedAt
            ? t("updatedAt", {
                time: settings.updatedAt.slice(0, 19).replace("T", " "),
              })
            : t("neverSaved")}
        </p>
        <Button type="submit" disabled={saving}>
          <Save className="size-4" />
          {saving ? t("saving") : t("save")}
        </Button>
      </div>
    </form>
  );
}

function GmailSetupGuide() {
  const t = useTranslations("settings");
  const [copied, setCopied] = useState(false);
  const redirectPath = "/api/gmail-oauth/callback";

  async function copyUri() {
    const absolute = `${window.location.origin}${redirectPath}`;
    try {
      await navigator.clipboard.writeText(absolute);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <details className="rounded-md border border-line px-3 py-2">
      <summary className="cursor-pointer text-sm font-medium text-fg">
        {t("gmail.guide.title")}
      </summary>
      <div className="mt-3 space-y-3 text-xs text-fg-muted">
        <div>
          <p className="font-medium text-fg">{t("gmail.guide.step1Title")}</p>
          <ol className="mt-1 list-decimal space-y-1 pl-4">
            <li>{t("gmail.guide.step1a")}</li>
            <li>{t("gmail.guide.step1b")}</li>
            <li>{t("gmail.guide.step1c")}</li>
          </ol>
        </div>
        <div>
          <p className="font-medium text-fg">{t("gmail.guide.step2Title")}</p>
          <ol className="mt-1 list-decimal space-y-1 pl-4">
            <li>{t("gmail.guide.step2a")}</li>
            <li>{t("gmail.guide.step2b")}</li>
            <li>{t("gmail.guide.step2c")}</li>
          </ol>
          <div className="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-warn/30 bg-warn-muted/30 px-3 py-2">
            <code className="min-w-0 flex-1 break-all text-fg">{redirectPath}</code>
            <Button type="button" size="sm" variant="outline" onClick={() => void copyUri()}>
              {copied ? t("gmail.guide.copied") : t("gmail.guide.copy")}
            </Button>
          </div>
        </div>
        <div>
          <p className="font-medium text-fg">{t("gmail.guide.step3Title")}</p>
          <ol className="mt-1 list-decimal space-y-1 pl-4">
            <li>{t("gmail.guide.step3a")}</li>
            <li>{t("gmail.guide.step3b")}</li>
            <li>{t("gmail.guide.step3c")}</li>
          </ol>
        </div>
        <p>{t("gmail.guide.notes")}</p>
      </div>
    </details>
  );
}

function Field({
  label,
  htmlFor,
  hint,
  children,
}: {
  label?: string;
  htmlFor?: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div>
      {label ? <Label htmlFor={htmlFor}>{label}</Label> : null}
      {children}
      {hint ? <p className="mt-1.5 text-xs text-fg-subtle">{hint}</p> : null}
    </div>
  );
}
