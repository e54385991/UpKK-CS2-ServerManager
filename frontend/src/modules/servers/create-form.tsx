"use client";

import {
  useCallback,
  useEffect,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { Route } from "next";
import { useTranslations } from "next-intl";
import { Check, Copy, Plus, RefreshCw, TriangleAlert } from "lucide-react";
import {
  applyAptMirrorAction,
  createServerAction,
} from "@/modules/servers/actions";
import type { ServerCreateResult } from "@/modules/servers/api";
import { AptMirrorSwitcher } from "@/modules/servers/apt-mirror-switcher";
import {
  APT_MIRRORS,
  toAptMirror,
  type AptMirrorId,
} from "@/modules/servers/apt-mirrors";
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
import { Textarea } from "@/shared/ui/textarea";
import { fetchCaptchaChallenge } from "@/shared/lib/captcha";
import { cn } from "@/shared/lib/cn";

type Captcha = { token: string; imageUrl: string };

const GAME_MODES = [
  "competitive",
  "casual",
  "wingman",
  "deathmatch",
  "armsrace",
  "demolition",
  "custom",
] as const;

export function CreateServerForm() {
  const t = useTranslations("serverNew");
  const router = useRouter();
  const [captcha, setCaptcha] = useState<Captcha | null>(null);
  const [captchaLoading, setCaptchaLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<ServerCreateResult | null>(null);
  const [copied, setCopied] = useState(false);
  const [sshUser, setSshUser] = useState("");
  const [aptMirror, setAptMirror] = useState<AptMirrorId>("official");
  const [switchingMirror, setSwitchingMirror] = useState<AptMirrorId | null>(
    null,
  );
  const isRoot = sshUser.trim().toLowerCase() === "root";

  const requestCaptcha = useCallback(async (): Promise<Captcha | null> => {
    return fetchCaptchaChallenge();
  }, []);

  useEffect(() => {
    let active = true;
    void requestCaptcha().then((next) => {
      if (!active) return;
      if (next) setCaptcha(next);
      else setError(t("captchaLoadError"));
      setCaptchaLoading(false);
    });
    return () => {
      active = false;
    };
  }, [requestCaptcha, t]);

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
    setError(null);
    setPending(true);
    const form = new FormData(event.currentTarget);
    const result = await createServerAction({
      name: String(form.get("name") ?? "").trim(),
      host: String(form.get("host") ?? "").trim(),
      sshPort: Number(form.get("sshPort") ?? 22),
      sshUser: String(form.get("sshUser") ?? "").trim(),
      sshPassword: String(form.get("sshPassword") ?? ""),
      sudoPassword: String(form.get("sudoPassword") ?? "") || undefined,
      aptMirror,
      gamePort: Number(form.get("gamePort") ?? 27015),
      gameDirectory: String(form.get("gameDirectory") ?? "").trim(),
      description: String(form.get("description") ?? "") || undefined,
      captchaToken: captcha.token,
      captchaCode: String(form.get("captcha") ?? "").trim(),
      serverName: String(form.get("serverName") ?? "").trim(),
      defaultMap: String(form.get("defaultMap") ?? "").trim(),
      maxPlayers: Number(form.get("maxPlayers") ?? 32),
      gameMode: String(form.get("gameMode") ?? "competitive"),
      gameType: String(form.get("gameType") ?? "0"),
      rconPassword: String(form.get("rconPassword") ?? "") || undefined,
      steamAccountToken: String(form.get("steamAccountToken") ?? "") || undefined,
      sessionManager:
        form.get("sessionManager") === "screen" ? "screen" : "tmux",
    });
    setPending(false);
    if (!result.ok) {
      setError(result.error);
      refreshCaptcha();
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
      setError(result.error);
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

  return (
    <form onSubmit={onSubmit} className="space-y-6">
      {error ? (
        <div className="flex items-start gap-2 rounded-md border border-danger/30 bg-danger-muted/50 px-3 py-2 text-sm text-danger">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      {created ? (
        <Card className="border-warn/30 bg-warn-muted/40">
          <CardHeader>
            <div>
              <CardTitle>{t("initPartialTitle")}</CardTitle>
              <CardDescription>{t("initPartialHelp")}</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {created.missingPackages.length > 0 ? (
              <p className="text-sm text-fg">
                {t("missingPackages", {
                  packages: created.missingPackages.join(", "),
                })}
              </p>
            ) : null}
            {created.initializationMessage ? (
              <p className="text-sm text-fg-muted">{created.initializationMessage}</p>
            ) : null}
            <div className="space-y-2">
              <p className="text-xs font-medium uppercase tracking-wide text-fg-subtle">
                {t("fields.aptMirror")}
              </p>
              <AptMirrorSwitcher
                current={toAptMirror(created.aptMirror)}
                disabled={switchingMirror !== null}
                busyMirror={switchingMirror}
                onSelect={(mirror) => void onSwitchMirror(mirror)}
                labelFor={(mirror) => t(`mirrors.${mirror}`)}
                applyLabel={t("switchMirror")}
              />
            </div>
            {created.manualInstallCommand ? (
              <div className="flex items-start gap-2">
                <pre className="min-w-0 flex-1 overflow-x-auto rounded-md border border-line bg-canvas px-3 py-2 font-mono text-xs text-fg">
                  {created.manualInstallCommand}
                </pre>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={() => void copyManualCommand()}
                  aria-label={t("copyCommand")}
                >
                  {copied ? <Check /> : <Copy />}
                </Button>
              </div>
            ) : null}
            <div className="flex flex-wrap gap-2">
              <Button asChild>
                <Link href={`/servers/${created.id}/operations` as Route}>
                  {t("goToOperations")}
                </Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>{t("connectionTitle")}</CardTitle>
              <CardDescription>{t("connectionHelp")}</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <Field className="sm:col-span-2" label={t("fields.name")} htmlFor="name">
              <Input id="name" name="name" required maxLength={255} autoFocus />
            </Field>
            <Field label={t("fields.host")} htmlFor="host">
              <Input id="host" name="host" required autoComplete="off" />
            </Field>
            <Field label={t("fields.gamePort")} htmlFor="gamePort">
              <Input
                id="gamePort"
                name="gamePort"
                type="number"
                min={1}
                max={65535}
                defaultValue={27015}
                required
              />
            </Field>
            <Field
              label={t("fields.sshUser")}
              htmlFor="sshUser"
              hint={t("privilegedHelp")}
            >
              <Input
                id="sshUser"
                name="sshUser"
                required
                autoComplete="off"
                value={sshUser}
                onChange={(event) => setSshUser(event.target.value)}
              />
            </Field>
            <Field label={t("fields.sshPort")} htmlFor="sshPort">
              <Input
                id="sshPort"
                name="sshPort"
                type="number"
                min={1}
                max={65535}
                defaultValue={22}
                required
              />
            </Field>
            <Field
              label={isRoot ? t("fields.sshPasswordRoot") : t("fields.sshPassword")}
              htmlFor="sshPassword"
              hint={isRoot ? t("sshPasswordRootHelp") : t("sshPasswordHelp")}
            >
              <Input
                id="sshPassword"
                name="sshPassword"
                type="password"
                required
                autoComplete="new-password"
              />
            </Field>
            <Field
              label={
                isRoot
                  ? t("fields.sudoPassword")
                  : t("fields.sudoPasswordRequired")
              }
              htmlFor="sudoPassword"
              hint={isRoot ? t("sudoPasswordRootHelp") : t("sudoPasswordHelp")}
            >
              <Input
                id="sudoPassword"
                name="sudoPassword"
                type="password"
                required={!isRoot}
                autoComplete="new-password"
              />
            </Field>
            <Field
              className="sm:col-span-2"
              label={t("fields.aptMirror")}
              htmlFor="aptMirror"
              hint={t("aptMirrorHelp")}
            >
              <Select
                id="aptMirror"
                name="aptMirror"
                value={aptMirror}
                onChange={(event) =>
                  setAptMirror(toAptMirror(event.target.value) ?? "official")
                }
              >
                {APT_MIRRORS.map((mirror) => (
                  <option key={mirror} value={mirror}>
                    {t(`mirrors.${mirror}`)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field
              className="sm:col-span-2"
              label={t("fields.gameDirectory")}
              htmlFor="gameDirectory"
            >
              <Input
                id="gameDirectory"
                name="gameDirectory"
                defaultValue="/home/cs2server/cs2"
                required
                className="font-mono"
              />
            </Field>
            <Field
              className="sm:col-span-2"
              label={t("fields.description")}
              htmlFor="description"
            >
              <Textarea id="description" name="description" rows={3} />
            </Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>{t("gameTitle")}</CardTitle>
              <CardDescription>{t("gameHelp")}</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <Field className="sm:col-span-2" label={t("fields.serverName")} htmlFor="serverName">
              <Input id="serverName" name="serverName" defaultValue="CS2 Server" required />
            </Field>
            <Field label={t("fields.defaultMap")} htmlFor="defaultMap">
              <Input id="defaultMap" name="defaultMap" defaultValue="de_dust2" required />
            </Field>
            <Field label={t("fields.maxPlayers")} htmlFor="maxPlayers">
              <Input
                id="maxPlayers"
                name="maxPlayers"
                type="number"
                min={1}
                max={64}
                defaultValue={32}
                required
              />
            </Field>
            <Field label={t("fields.gameMode")} htmlFor="gameMode">
              <Select id="gameMode" name="gameMode" defaultValue="competitive">
                {GAME_MODES.map((mode) => (
                  <option key={mode} value={mode}>
                    {t(`modes.${mode}`)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={t("fields.gameType")} htmlFor="gameType">
              <Input id="gameType" name="gameType" defaultValue="0" required />
            </Field>
            <Field label={t("fields.sessionManager")} htmlFor="sessionManager">
              <Select id="sessionManager" name="sessionManager" defaultValue="tmux">
                <option value="tmux">tmux</option>
                <option value="screen">screen</option>
              </Select>
            </Field>
            <Field label={t("fields.rconPassword")} htmlFor="rconPassword">
              <Input
                id="rconPassword"
                name="rconPassword"
                type="password"
                autoComplete="new-password"
              />
            </Field>
            <Field
              className="sm:col-span-2"
              label={t("fields.steamAccountToken")}
              htmlFor="steamAccountToken"
            >
              <Input
                id="steamAccountToken"
                name="steamAccountToken"
                type="password"
                autoComplete="off"
              />
            </Field>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("confirmTitle")}</CardTitle>
            <CardDescription>{t("confirmHelp")}</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 sm:flex-row sm:items-end">
          <div className="min-w-0 flex-1">
            <Label htmlFor="captcha">{t("fields.captcha")}</Label>
            <div className="flex items-center gap-3">
              <Input
                id="captcha"
                name="captcha"
                required
                maxLength={4}
                autoComplete="off"
                className="uppercase tracking-[0.3em]"
                placeholder={t("captchaPlaceholder")}
              />
              <button
                type="button"
                onClick={refreshCaptcha}
                aria-label={t("refreshCaptcha")}
                className="relative flex h-10 w-28 shrink-0 items-center justify-center overflow-hidden rounded-md border border-line bg-surface"
              >
                {captcha && !captchaLoading ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={captcha.imageUrl}
                    alt={t("fields.captcha")}
                    className="h-full w-full object-contain"
                  />
                ) : (
                  <span className="text-xs text-fg-subtle">{t("loading")}</span>
                )}
                <span className="absolute right-1 top-1 rounded bg-canvas/70 p-0.5 text-fg-subtle">
                  <RefreshCw
                    className={cn("size-3", captchaLoading && "animate-spin")}
                  />
                </span>
              </button>
            </div>
          </div>
          <Button type="submit" disabled={pending || !captcha}>
            <Plus />
            {pending ? t("submitting") : t("submit")}
          </Button>
        </CardContent>
      </Card>
    </form>
  );
}

function Field({
  label,
  htmlFor,
  children,
  className,
  hint,
}: {
  label: string;
  htmlFor: string;
  children: ReactNode;
  className?: string;
  hint?: string;
}) {
  return (
    <div className={className}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint ? <p className="mt-1 text-xs text-fg-subtle">{hint}</p> : null}
    </div>
  );
}
