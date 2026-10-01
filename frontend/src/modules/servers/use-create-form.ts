"use client";

import {
applyAptMirrorAction,
createServerAction,
} from "@/modules/servers/actions";
import type { ServerCreateResult } from "@/modules/servers/api";
import {
type AptMirrorId
} from "@/modules/servers/apt-mirrors";
import {
isInvalidGameDirectory,
parseHostDirectoryConflict,
pickInitializedHost,
rememberInitializedHost,
setupWizardHref,
suggestedDeployPort,
} from "@/modules/servers/initialized-hosts";
import {
getInitializedHostCredentialsAction,
listInitializedHostsAction,
} from "@/modules/servers/setup-actions";
import type {
InitializedHost,
InitializedHostCredentials,
} from "@/modules/servers/setup-api";
import { alertDialog } from "@/shared/feedback/alert-store";
import { fetchCaptchaChallenge } from "@/shared/lib/captcha";
import type { Route } from "next";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import {
useCallback,
useEffect,
useState,
type FormEvent
} from "react";

import { type Captcha } from "./create-form-parts";

export function useCreateServerForm({
  initialCredentials,
  markedInitializedHost,
}: {
  initialCredentials?: InitializedHostCredentials;
  markedInitializedHost?: string;
} = {}) {
  const t = useTranslations("serverNew");
  const router = useRouter();
  const [captcha, setCaptcha] = useState<Captcha | null>(null);
  const [captchaLoading, setCaptchaLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<ServerCreateResult | null>(null);
  const [copied, setCopied] = useState(false);
  const [hosts, setHosts] = useState<InitializedHost[]>([]);
  const [hostsLoading, setHostsLoading] = useState(true);
  const [account, setAccount] = useState<InitializedHostCredentials | undefined>(
    initialCredentials,
  );
  const [selectedHostKey, setSelectedHostKey] = useState(
    initialCredentials?.key ?? "",
  );
  const [displayName, setDisplayName] = useState(initialCredentials?.name ?? "");
  const [gameDirectory, setGameDirectory] = useState(initialCredentials?.gameDirectory ?? "");
  const [gamePort, setGamePort] = useState("27015");
  const [aptMirror, setAptMirror] = useState<AptMirrorId>("official");
  const [steamAccountToken, setSteamAccountToken] = useState("");
  const [additionalParameters, setAdditionalParameters] = useState("");
  const [switchingMirror, setSwitchingMirror] = useState<AptMirrorId | null>(
    null,
  );
  const isRoot = (account?.sshUser ?? "").trim().toLowerCase() === "root";
  const canCreate = Boolean(account && selectedHostKey);

  const requestCaptcha = useCallback(async (): Promise<Captcha | null> => {
    return fetchCaptchaChallenge();
  }, []);

  const applyInitializedHost = useCallback(
    async (key: string) => {
      if (!key) {
        setSelectedHostKey("");
        setAccount(undefined);
        setGameDirectory("");
        setGamePort("27015");
        return;
      }
      const creds = await getInitializedHostCredentialsAction(key);
      if (!creds.ok) {
        void alertDialog({
          title: t("errorTitle"),
          description: creds.error,
        });
        return;
      }
      rememberInitializedHost(creds.data.host);
      setSelectedHostKey(key);
      setAccount(creds.data);
      setGameDirectory(creds.data.gameDirectory);
      setGamePort(
        suggestedDeployPort(hosts.find((item) => item.key === key)),
      );
      setDisplayName((current) =>
        current.trim() === "" ? creds.data.name : current,
      );
    },
    [hosts, t],
  );

  useEffect(() => {
    let active = true;
    void requestCaptcha().then((next) => {
      if (!active) return;
      if (next) setCaptcha(next);
      else setError(t("captchaLoadError"));
      setCaptchaLoading(false);
    });
    void (async () => {
      const listed = await listInitializedHostsAction();
      if (!active) return;
      if (!listed.ok) {
        setHosts([]);
        setHostsLoading(false);
        return;
      }
      setHosts(listed.data);
      if (initialCredentials?.key) {
        rememberInitializedHost(initialCredentials.host);
        setGamePort(suggestedDeployPort(listed.data.find((item) => item.key === initialCredentials.key)));
        setHostsLoading(false);
        return;
      }
      const match = pickInitializedHost(listed.data, {
        markedHost: markedInitializedHost,
      });
      if (!match) {
        setHostsLoading(false);
        return;
      }
      const creds = await getInitializedHostCredentialsAction(match.key);
      if (!active) return;
      if (creds.ok) {
        rememberInitializedHost(creds.data.host);
        setSelectedHostKey(match.key);
        setAccount(creds.data);
        setGameDirectory(creds.data.gameDirectory);
        setGamePort(suggestedDeployPort(match));
        setDisplayName((current) =>
          current.trim() === "" ? creds.data.name : current,
        );
      }
      setHostsLoading(false);
    })();
    return () => {
      active = false;
    };
  }, [initialCredentials, markedInitializedHost, requestCaptcha, t]);

  const refreshCaptcha = useCallback(() => {
    setCaptchaLoading(true);
    void requestCaptcha().then((next) => {
      setCaptcha((prev) => {
        if (prev?.imageUrl.startsWith("blob:")) {
          URL.revokeObjectURL(prev.imageUrl);
        }
        return next ?? prev;
      });
      if (!next) setError(t("captchaLoadError"));
      setCaptchaLoading(false);
    });
  }, [requestCaptcha, t]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!captcha) return;
    const form = new FormData(event.currentTarget);
    const name = String(form.get("name") ?? displayName).trim();
    if (!account || !selectedHostKey) {
      await alertDialog({
        title: t("mustInitializeTitle"),
        description: t("mustInitialize"),
      });
      router.push(setupWizardHref({ name }) as Route);
      return;
    }
    const nextDirectory = gameDirectory.trim();
    if (isInvalidGameDirectory(nextDirectory)) {
      await alertDialog({
        title: t("errorTitle"),
        description: t("invalidGameDirectory"),
      });
      return;
    }
    setError(null);
    setPending(true);
    const result = await createServerAction({
      name,
      host: account.host,
      sshPort: account.sshPort,
      sshUser: account.sshUser,
      sshPassword: account.sshPassword,
      aptMirror,
      gamePort: Number(form.get("gamePort") ?? gamePort),
      gameDirectory: nextDirectory,
      description: String(form.get("description") ?? "") || undefined,
      captchaToken: captcha.token,
      captchaCode: String(form.get("captcha") ?? "").trim(),
      serverName: String(form.get("serverName") ?? "").trim(),
      defaultMap: String(form.get("defaultMap") ?? "").trim(),
      maxPlayers: Number(form.get("maxPlayers") ?? 32),
      gameMode: String(form.get("gameMode") ?? "competitive"),
      gameType: String(form.get("gameType") ?? "0"),
      rconPassword: String(form.get("rconPassword") ?? "") || undefined,
      steamAccountToken: steamAccountToken.trim() || undefined,
      additionalParameters: additionalParameters.trim() || undefined,
      sessionManager:
        form.get("sessionManager") === "screen" ? "screen" : "tmux",
    });
    setPending(false);
    if (!result.ok) {
      refreshCaptcha();
      const conflict = parseHostDirectoryConflict(result.status, result.error, result.detail);
      if (conflict) {
        const { confirm: confirmDialog } = await import("@/shared/feedback/confirm-store");
        const confirmed = await confirmDialog({
          title: t("directoryExistsTitle"),
          description: t("directoryExistsHelp", {
            name: conflict.existingServerName || conflict.host,
            directory: conflict.gameDirectory,
          }),
          confirmLabel: t("directoryExistsConfirm"),
        });
        if (confirmed) {
          router.push(`/servers/${conflict.existingServerId}/operations` as Route);
          router.refresh();
          return;
        }
        return;
      }
      void alertDialog({
        title: t("errorTitle"),
        description: result.error,
      });
      return;
    }
    if (result.data.hostInitialized) {
      router.push(`/servers/${result.data.id}/operations` as Route);
      router.refresh();
      return;
    }
    setCreated(result.data);
  }

  async function onSwitchMirror(mirror: AptMirrorId) {
    if (!created) return;
    setError(null);
    setSwitchingMirror(mirror);
    const result = await applyAptMirrorAction(created.id, mirror);
    setSwitchingMirror(null);
    if (!result.ok) {
      void alertDialog({
        title: t("errorTitle"),
        description: result.error,
      });
      return;
    }
    router.push(`/servers/${created.id}/operations` as Route);
    router.refresh();
  }

  async function copyManualCommand() {
    if (!created?.manualInstallCommand) return;
    try {
      await navigator.clipboard.writeText(created.manualInstallCommand);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  const showCreateFields = !hostsLoading && hosts.length > 0;

  
  return {
    t,
    captcha,
    captchaLoading,
    pending,
    error,
    created,
    copied,
    hosts,
    hostsLoading,
    account,
    selectedHostKey,
    displayName,
    setDisplayName,
    gameDirectory,
    setGameDirectory,
    gamePort,
    setGamePort,
    aptMirror,
    setAptMirror,
    steamAccountToken,
    setSteamAccountToken,
    additionalParameters,
    setAdditionalParameters,
    switchingMirror,
    isRoot,
    canCreate,
    applyInitializedHost,
    refreshCaptcha,
    onSubmit,
    onSwitchMirror,
    copyManualCommand,
    showCreateFields,
  };
}
