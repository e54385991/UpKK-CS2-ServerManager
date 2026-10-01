"use client";

import {
  authorizeGmailAction,
  refreshSettingsAction,
  revokeGmailAction,
  saveSettingsAction,
  sendTestEmailAction,
  uploadGmailCredentialsAction,
} from "@/modules/settings/actions";
import {
  AUDIT_LOG_RETENTION_DAYS_MAX,
  AUDIT_LOG_RETENTION_DAYS_MIN,
  clientIpChoiceOf,
  clientIpHeaderOf,
  customClientIpOf,
  ENVIRONMENT_LOG_LEVEL,
  logLevelOf
} from "@/modules/settings/runtime-cards";
import {
  isClientIpHeader,
  isGoogleClientId,
  type EmailProvider,
  type ProxyMode,
  type SystemSettings,
} from "@/modules/settings/types";
import { confirm } from "@/shared/feedback";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { type Banner, type SettingsSectionKey } from "./settings-form-parts";

export function useSettingsForm({
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
    const result = await authorizeGmailAction(window.location.origin);
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

  return {
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
  };
}
