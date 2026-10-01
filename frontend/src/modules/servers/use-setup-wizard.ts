"use client";

import { createServerAction } from "@/modules/servers/actions";
import {
  isCs2Username
} from "@/modules/servers/cs2-username";
import {
  rememberInitializedHost
} from "@/modules/servers/initialized-hosts";
import {
  deleteInitializedHostAction,
  getManualSetupScriptAction,
  listInitializedHostsAction,
} from "@/modules/servers/setup-actions";
import type {
  AutoSetupResult,
  InitializedHost,
  ManualSetupScript,
} from "@/modules/servers/setup-api";
import { runAutoSetupFromBrowser } from "@/modules/servers/setup-client";
import { alertDialog } from "@/shared/feedback/alert-store";
import { confirm as confirmDialog } from "@/shared/feedback/confirm-store";
import { fetchCaptchaChallenge } from "@/shared/lib/captcha";
import { copyText } from "@/shared/lib/clipboard";
import { randomId } from "@/shared/lib/random-id";
import type { Route } from "next";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent
} from "react";

import { type Captcha, type Mode, openSetupProgressSocket, waitForSocket } from "./setup-wizard-parts";

export function useSetupWizard({
  initialName = "",
  initialHost = "",
  initialSshPort = 22,
  initialSshUser = "",
  requireInit = false,
}: {
  initialName?: string;
  initialHost?: string;
  initialSshPort?: number;
  initialSshUser?: string;
  requireInit?: boolean;
}) {
  const t = useTranslations("setupWizard");
  const router = useRouter();
  const setupFormRef = useRef<HTMLFormElement>(null);
  const [mode, setMode] = useState<Mode>("auto");
  const [hosts, setHosts] = useState<InitializedHost[]>([]);
  const [captcha, setCaptcha] = useState<Captcha | null>(null);
  const [captchaLoading, setCaptchaLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [result, setResult] = useState<AutoSetupResult | null>(null);
  const [completedHost, setCompletedHost] = useState("");
  const [manual, setManual] = useState<ManualSetupScript | null>(null);
  const [cs2Username, setCs2Username] = useState("cs2server");
  const [copied, setCopied] = useState<string | null>(null);
  const [differentSudo, setDifferentSudo] = useState(false);
  const [setupFailed, setSetupFailed] = useState(false);

  const refreshCaptcha = useCallback(() => {
    setCaptchaLoading(true);
    void fetchCaptchaChallenge().then((next) => {
      setCaptcha((prev) => {
        if (prev?.imageUrl.startsWith("blob:")) URL.revokeObjectURL(prev.imageUrl);
        return next ?? prev;
      });
      if (!next) setError(t("captchaLoadError"));
      setCaptchaLoading(false);
    });
  }, [t]);

  useEffect(() => {
    let active = true;
    void fetchCaptchaChallenge().then((next) => {
      if (!active) return;
      if (next) setCaptcha(next);
      else setError(t("captchaLoadError"));
      setCaptchaLoading(false);
    });
    void listInitializedHostsAction().then((listed) => {
      if (active && listed.ok) setHosts(listed.data);
    });
    return () => {
      active = false;
    };
  }, [t]);

  useEffect(() => {
    if (!isCs2Username(cs2Username)) return;
    void getManualSetupScriptAction(cs2Username).then((script) => {
      if (script.ok) setManual(script.data);
    });
  }, [cs2Username]);

  async function onDelete(key: string) {
    const deleted = await deleteInitializedHostAction(key);
    if (!deleted.ok) {
      setError(deleted.error);
      return;
    }
    setHosts((current) => current.filter((host) => host.key !== key));
  }

  async function copy(value: string, id: string) {
    const ok = await copyText(value);
    setCopied(ok ? id : null);
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!captcha) return;
    const form = new FormData(event.currentTarget);
    const host = String(form.get("host") ?? "").trim();
    const sessionId = randomId();
    const appendLog = (message: string) => {
      setLogs((current) => [...current, message]);
    };

    setPending(true);
    setError(null);
    setResult(null);
    setSetupFailed(false);
    setLogs([]);
    appendLog(t("wsConnecting"));

    let socket: WebSocket | null = null;
    try {
      socket = openSetupProgressSocket(sessionId, appendLog);
      await waitForSocket(
        socket,
        () => appendLog(t("wsConnected")),
        () => appendLog(t("wsFailed")),
      );
      appendLog(t("running"));

      const submitted = await runAutoSetupFromBrowser({
        name: String(form.get("name") ?? "").trim(),
        host,
        sshPort: Number(form.get("sshPort") ?? 22),
        sshUser: String(form.get("sshUser") ?? "").trim(),
        sshPassword: String(form.get("sshPassword") ?? ""),
        sudoPassword: differentSudo
          ? String(form.get("sudoPassword") ?? "") || undefined
          : undefined,
        cs2Username: String(form.get("cs2Username") ?? "cs2server").trim(),
        captchaToken: captcha.token,
        captchaCode: String(form.get("captcha") ?? "").trim(),
        saveConfig: true,
        openGamePorts: form.get("openGamePorts") === "on",
        sessionId,
      });
      if (!submitted.ok) {
        setSetupFailed(true);
        appendLog(`${t("errorTitle")}: ${submitted.error}`);
        refreshCaptcha();
        void alertDialog({
          title: t("errorTitle"),
          description: submitted.error,
        });
        return;
      }
      if (!submitted.data.success) {
        setSetupFailed(true);
        appendLog(`${t("errorTitle")}: ${submitted.data.message}`);
        refreshCaptcha();
        return;
      }
      if (socket.readyState !== WebSocket.OPEN && submitted.data.logs.length > 0) {
        setLogs([...submitted.data.logs]);
      }
      rememberInitializedHost(host);
      setCompletedHost(host);
      setSetupFailed(false);
      setResult(submitted.data);
      refreshCaptcha();
      const listed = await listInitializedHostsAction();
      if (listed.ok) setHosts(listed.data);
    } catch (cause) {
      setSetupFailed(true);
      const message = cause instanceof Error ? cause.message : t("errorTitle");
      appendLog(`${t("errorTitle")}: ${message}`);
      refreshCaptcha();
      void alertDialog({
        title: t("errorTitle"),
        description: message,
      });
    } finally {
      if (socket?.readyState === WebSocket.OPEN) socket.close();
      setPending(false);
    }
  }

  async function onForceAdd() {
    const formElement = setupFormRef.current;
    if (!formElement) return;
    if (!captcha) return;
    const form = new FormData(formElement);
    const captchaCode = String(form.get("captcha") ?? "").trim();
    if (captcha.enabled && !captchaCode) {
      await alertDialog({
        title: t("forceAddTitle"),
        description: t("forceAddCaptchaRequired"),
      });
      return;
    }
    if (!formElement.reportValidity()) return;
    const confirmed = await confirmDialog({
      title: t("forceAddConfirmTitle"),
      description: t("forceAddConfirmHelp"),
      confirmLabel: t("forceAddConfirm"),
      tone: "danger",
    });
    if (!confirmed) return;

    const cs2Username = String(form.get("cs2Username") ?? "cs2server").trim();
    setPending(true);
    setError(null);
    try {
      const created = await createServerAction({
        name: String(form.get("name") ?? "").trim(),
        host: String(form.get("host") ?? "").trim(),
        sshPort: Number(form.get("sshPort") ?? 22),
        sshUser: String(form.get("sshUser") ?? "").trim(),
        sshPassword: String(form.get("sshPassword") ?? ""),
        sudoPassword: differentSudo
          ? String(form.get("sudoPassword") ?? "") || undefined
          : undefined,
        gamePort: 27015,
        gameDirectory: `/home/${cs2Username}/cs2`,
        captchaToken: captcha.token,
        captchaCode,
        forceAdd: true,
        serverName: "CS2 Server",
        defaultMap: "de_dust2",
        maxPlayers: 32,
        gameMode: "competitive",
        gameType: "0",
        sessionManager: "tmux",
      });
      if (!created.ok) {
        refreshCaptcha();
        await alertDialog({
          title: t("errorTitle"),
          description: created.error,
        });
        return;
      }
      router.push(`/servers/${created.data.id}/operations` as Route);
      router.refresh();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : t("errorTitle");
      await alertDialog({
        title: t("errorTitle"),
        description: message,
      });
    } finally {
      setPending(false);
    }
  }

  return {
    t,
    setupFormRef,
    mode,
    setMode,
    hosts,
    captcha,
    captchaLoading,
    pending,
    error,
    logs,
    result,
    completedHost,
    manual,
    cs2Username,
    setCs2Username,
    copied,
    differentSudo,
    setDifferentSudo,
    setupFailed,
    refreshCaptcha,
    onDelete,
    copy,
    onSubmit,
    onForceAdd,
  };
}
