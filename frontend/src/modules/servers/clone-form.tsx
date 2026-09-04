"use client";

import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { Route } from "next";
import { useTranslations } from "next-intl";
import { Check, Plus, RefreshCw, TriangleAlert } from "lucide-react";
import { cloneServerAction, getServerCloneTemplateAction } from "@/modules/servers/actions";
import type { ServerCloneInput, ServerCloneTemplate } from "@/modules/servers/api";
import { AdditionalParametersField, OfficialMapField } from "@/modules/servers/additional-parameters-field";
import { GsltTokenField } from "@/modules/servers/gslt-token-field";
import { toAptMirror } from "@/modules/servers/apt-mirrors";
import { Button } from "@/shared/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/shared/ui/card";
import { Dialog } from "@/shared/ui/dialog";
import { Input, Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";
import { Textarea } from "@/shared/ui/textarea";
import { fetchCaptchaChallenge } from "@/shared/lib/captcha";
import { cn } from "@/shared/lib/cn";

type Captcha = { token: string; imageUrl: string; enabled: boolean };

const GAME_MODES = [
  "competitive",
  "casual",
  "wingman",
  "deathmatch",
  "armsrace",
  "demolition",
  "custom",
] as const;

function canonicalDirectory(value: string): string {
  const raw = value.trim();
  if (!raw.startsWith("/")) return raw;
  const parts: string[] = [];
  for (const part of raw.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") parts.pop();
    else parts.push(part);
  }
  return `/${parts.join("/")}` || "/";
}

export function CloneServerForm({ template }: { template: ServerCloneTemplate }) {
  const t = useTranslations("serverNew");
  const router = useRouter();
  const [captcha, setCaptcha] = useState<Captcha | null>(null);
  const [captchaLoading, setCaptchaLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [availableHint, setAvailableHint] = useState<Pick<ServerCloneTemplate, "name" | "gamePort" | "gameDirectory"> | null>(null);
  const [preview, setPreview] = useState<ServerCloneInput | null>(null);
  const [targetName, setTargetName] = useState(template.name);
  const [serverName, setServerName] = useState(template.serverName);
  const [gamePort, setGamePort] = useState(String(template.gamePort));
  const [gameDirectory, setGameDirectory] = useState(template.gameDirectory);
  const [steamAccountToken, setSteamAccountToken] = useState("");
  const [additionalParameters, setAdditionalParameters] = useState(
    template.additionalParameters ?? "",
  );

  const loadCaptcha = useCallback(async () => fetchCaptchaChallenge(), []);

  useEffect(() => {
    let active = true;
    void loadCaptcha().then((next) => {
      if (!active) return;
      setCaptcha(next);
      setCaptchaLoading(false);
    });
    return () => {
      active = false;
    };
  }, [loadCaptcha]);

  const refreshCaptcha = useCallback(() => {
    setCaptchaLoading(true);
    void loadCaptcha().then((next) => {
      setCaptcha((current) => next ?? current);
      setCaptchaLoading(false);
    });
  }, [loadCaptcha]);

  const directoryIsInvalid =
    !gameDirectory.trim().startsWith("/") || canonicalDirectory(gameDirectory) === "/";
  const directoryIsSame =
    canonicalDirectory(gameDirectory) === canonicalDirectory(template.sourceGameDirectory);
  const numericGamePort = Number(gamePort);
  const portIsInvalid =
    !Number.isInteger(numericGamePort) || numericGamePort < 1 || numericGamePort > 65534;
  const portIsSame = numericGamePort === template.sourceGamePort;
  const targetNameIsEmpty = targetName.trim() === "";
  const serverNameIsEmpty = serverName.trim() === "";
  const gameModeOptions = GAME_MODES.includes(template.gameMode as (typeof GAME_MODES)[number])
    ? GAME_MODES
    : [template.gameMode, ...GAME_MODES];
  const canPreview = Boolean(
    captcha &&
      !captchaLoading &&
      !pending &&
      !directoryIsInvalid &&
      !directoryIsSame &&
      !portIsInvalid &&
      !portIsSame &&
      !targetNameIsEmpty &&
      !serverNameIsEmpty,
  );

  function collectValues(form: HTMLFormElement): ServerCloneInput {
    const value = (name: string) => String(new FormData(form).get(name) ?? "").trim();
    return {
      name: targetName.trim(),
      gamePort: numericGamePort,
      gameDirectory: gameDirectory.trim(),
      description: value("description") || undefined,
      serverName: serverName.trim(),
      defaultMap: value("defaultMap"),
      maxPlayers: Number(value("maxPlayers")),
      gameMode: value("gameMode"),
      gameType: value("gameType"),
      sessionManager: value("sessionManager") === "screen" ? "screen" : "tmux",
      aptMirror: toAptMirror(value("aptMirror")) ?? undefined,
      sudoPassword: value("sudoPassword") || undefined,
      rconPassword: value("rconPassword") || undefined,
      steamAccountToken: steamAccountToken.trim() || undefined,
      additionalParameters: additionalParameters.trim(),
      captchaToken: captcha?.enabled === false ? "" : captcha?.token ?? "",
      captchaCode: value("captcha"),
    };
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canPreview) return;
    setError(null);
    setPreview(collectValues(event.currentTarget));
  }

  async function confirmClone() {
    if (!preview) return;
    setPending(true);
    setError(null);
    setAvailableHint(null);
    const result = await cloneServerAction(template.sourceServerId, preview);
    setPending(false);
    if (!result.ok) {
      setPreview(null);
      setError(result.error);
      if (result.status === 409) {
        const refreshed = await getServerCloneTemplateAction(template.sourceServerId);
        if (refreshed.ok) {
          setAvailableHint({
            name: refreshed.data.name,
            gamePort: refreshed.data.gamePort,
            gameDirectory: refreshed.data.gameDirectory,
          });
        }
      }
      refreshCaptcha();
      return;
    }
    setPreview(null);
    router.push(`/servers/${result.data.id}/operations` as Route);
    router.refresh();
  }

  return (
    <>
      <form onSubmit={onSubmit} className="space-y-6">
        {error ? (
          <div className="flex items-start gap-2 rounded-md border border-danger/30 bg-danger-muted/50 px-3 py-2 text-sm text-danger">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            <span>
              {error}
              {availableHint ? (
                <span className="mt-1 block text-xs">
                  {t("clone.conflictHint", {
                    name: availableHint.name,
                    gamePort: String(availableHint.gamePort),
                    gameDirectory: availableHint.gameDirectory,
                  })}
                </span>
              ) : null}
            </span>
          </div>
        ) : null}

        <Card className="border-primary/20 bg-primary/5">
          <CardHeader>
            <div>
              <CardTitle>{t("clone.sourceTitle")}</CardTitle>
              <CardDescription>{t("clone.sourceHelp")}</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <Summary label={t("fields.name")} value={template.sourceName} />
            <Summary label={t("fields.host")} value={template.host} />
            <Summary label={t("fields.sshUser")} value={template.sshUser} />
            <Summary label={t("fields.sshPort")} value={String(template.sshPort)} />
            <Summary label={t("fields.gameDirectory")} value={template.sourceGameDirectory} />
            <Summary label={t("clone.sshPassword")} value={t("clone.reusedSecret")} />
            <Field
              className="sm:col-span-2"
              label={t("fields.sudoPassword")}
              htmlFor="sudoPassword"
              hint={template.hasSudoPassword ? t("clone.sudoHelp") : t("clone.sudoFallbackHelp")}
            >
              <Input id="sudoPassword" name="sudoPassword" type="password" autoComplete="new-password" />
            </Field>
            <div className="sm:col-span-2 rounded-md border border-line bg-canvas/60 px-3 py-2 text-xs text-fg-subtle">
              {template.hasSudoPassword ? t("clone.sudoReused") : t("clone.sudoFallback")}
            </div>
          </CardContent>
        </Card>

        <div className="grid gap-6 xl:grid-cols-2">
          <Card>
            <CardHeader>
              <div>
                <CardTitle>{t("clone.targetTitle")}</CardTitle>
                <CardDescription>{t("clone.targetHelp")}</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="grid gap-4 sm:grid-cols-2">
              <Field className="sm:col-span-2" label={t("fields.name")} htmlFor="name">
                <Input
                  id="name"
                  name="name"
                  required
                  maxLength={255}
                  value={targetName}
                  onChange={(event) => {
                    const value = event.target.value;
                    setTargetName(value);
                    if (serverName === template.serverName) setServerName(value);
                  }}
                />
              </Field>
              <Field
                label={t("fields.gamePort")}
                htmlFor="gamePort"
                hint={portIsSame ? t("clone.samePort") : portIsInvalid ? t("clone.portHelp") : undefined}
              >
                <Input
                  id="gamePort"
                  name="gamePort"
                  type="number"
                  min={1}
                  max={65534}
                  required
                  value={gamePort}
                  onChange={(event) => setGamePort(event.target.value)}
                  className={cn(portIsSame && "border-danger")}
                />
              </Field>
              <Field
                className="sm:col-span-2"
                label={t("fields.gameDirectory")}
                htmlFor="gameDirectory"
                hint={directoryIsSame ? t("clone.sameDirectory") : directoryIsInvalid ? t("clone.directoryHelp") : undefined}
              >
                <Input
                  id="gameDirectory"
                  name="gameDirectory"
                  required
                  maxLength={500}
                  value={gameDirectory}
                  onChange={(event) => setGameDirectory(event.target.value)}
                  className={cn((directoryIsSame || directoryIsInvalid) && "border-danger")}
                />
              </Field>
              <Field className="sm:col-span-2" label={t("fields.description")} htmlFor="description">
                <Textarea id="description" name="description" rows={3} />
              </Field>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div>
                <CardTitle>{t("gameTitle")}</CardTitle>
                <CardDescription>{t("clone.gameHelp")}</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="grid gap-4 sm:grid-cols-2">
              <Field className="sm:col-span-2" label={t("fields.serverName")} htmlFor="serverName">
                <Input id="serverName" name="serverName" required value={serverName} onChange={(event) => setServerName(event.target.value)} />
              </Field>
              <OfficialMapField id="defaultMap" name="defaultMap" defaultValue={template.defaultMap} />
              <Field label={t("fields.maxPlayers")} htmlFor="maxPlayers">
                <Input id="maxPlayers" name="maxPlayers" type="number" min={1} max={64} required defaultValue={template.maxPlayers} />
              </Field>
              <Field label={t("fields.gameMode")} htmlFor="gameMode">
                <Select id="gameMode" name="gameMode" defaultValue={template.gameMode}>
                  {gameModeOptions.map((mode) => (
                    <option key={mode} value={mode}>
                      {GAME_MODES.includes(mode as (typeof GAME_MODES)[number])
                        ? t(`modes.${mode}`)
                        : mode}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label={t("fields.gameType")} htmlFor="gameType">
                <Input id="gameType" name="gameType" defaultValue={template.gameType} required />
              </Field>
              <Field label={t("fields.sessionManager")} htmlFor="sessionManager">
                <Select id="sessionManager" name="sessionManager" defaultValue={template.sessionManager}>
                  <option value="tmux">tmux</option>
                  <option value="screen">screen</option>
                </Select>
              </Field>
              <Field label={t("fields.rconPassword")} htmlFor="rconPassword">
                <Input id="rconPassword" name="rconPassword" type="password" autoComplete="new-password" />
              </Field>
              <GsltTokenField
                className="sm:col-span-2"
                id="steamAccountToken"
                name="steamAccountToken"
                label={t("fields.steamAccountToken")}
                value={steamAccountToken}
                serverName={targetName || undefined}
                onChange={setSteamAccountToken}
              />
              <AdditionalParametersField
                className="sm:col-span-2"
                id="additionalParameters"
                name="additionalParameters"
                value={additionalParameters}
                onChange={setAdditionalParameters}
              />
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>{t("clone.copyTitle")}</CardTitle>
              <CardDescription>{t("clone.copyHelp")}</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-fg-muted">
              {t("clone.proxy", { value: template.usePanelProxy ? t("clone.panelProxy") : template.githubProxy ?? t("clone.directProxy") })}
            </p>
            <p className="text-sm text-fg-muted">
              {t("clone.apt", { value: template.aptMirror ?? t("clone.hostDefault") })}
            </p>
            <p className="text-sm text-fg-muted">{t("clone.noFiles")}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>{t("confirmTitle")}</CardTitle>
              <CardDescription>{t("clone.confirmHelp")}</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-4 sm:flex-row sm:items-end">
            {captcha?.enabled !== false ? (
              <div className="min-w-0 flex-1">
                <Label htmlFor="captcha">{t("fields.captcha")}</Label>
                <div className="flex items-center gap-3">
                  <Input id="captcha" name="captcha" required maxLength={4} autoComplete="off" className="uppercase tracking-[0.3em]" placeholder={t("captchaPlaceholder")} />
                  <button type="button" onClick={refreshCaptcha} aria-label={t("refreshCaptcha")} className="relative flex h-10 w-28 shrink-0 items-center justify-center overflow-hidden rounded-md border border-line bg-surface">
                    {captcha && !captchaLoading ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={captcha.imageUrl} alt={t("fields.captcha")} className="h-full w-full object-contain" />
                    ) : <span className="text-xs text-fg-subtle">{t("loading")}</span>}
                    <span className="absolute right-1 top-1 rounded bg-canvas/70 p-0.5 text-fg-subtle"><RefreshCw className={cn("size-3", captchaLoading && "animate-spin")} /></span>
                  </button>
                </div>
              </div>
            ) : null}
            <Button type="submit" disabled={!canPreview} data-testid="clone-preview-button">
              <Check />
              {t("clone.preview")}
            </Button>
            <Button asChild type="button" variant="outline">
              <Link href={`/servers/${template.sourceServerId}` as Route}>{t("clone.backToSource")}</Link>
            </Button>
          </CardContent>
        </Card>
      </form>

      <Dialog
        open={preview !== null}
        title={t("clone.previewTitle")}
        description={t("clone.previewHelp")}
        closeLabel={t("clone.cancel")}
        onClose={() => { if (!pending) setPreview(null); }}
        footer={
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" disabled={pending} onClick={() => setPreview(null)}>{t("clone.cancel")}</Button>
            <Button type="button" disabled={pending} onClick={() => void confirmClone()}>
              <Plus />
              {pending ? t("submitting") : t("clone.confirm")}
            </Button>
          </div>
        }
      >
        {preview ? <PreviewValues template={template} values={preview} t={t} /> : null}
      </Dialog>
    </>
  );
}

function PreviewValues({ template, values, t }: { template: ServerCloneTemplate; values: ServerCloneInput; t: (key: string, values?: Record<string, string>) => string }) {
  const secret = (value: string | undefined, fallback: string) => value ? t("clone.newSecret") : fallback;
  return (
    <div className="space-y-4 text-sm">
      <dl className="grid gap-3 sm:grid-cols-2">
        <Summary label={t("clone.source")} value={template.sourceName} />
        <Summary label={t("clone.sourceDirectory")} value={template.sourceGameDirectory} />
        <Summary label={t("fields.name")} value={values.name} />
        <Summary label={t("fields.host")} value={`${template.host}:${template.sshPort}`} />
        <Summary label={t("fields.sshUser")} value={template.sshUser} />
        <Summary label={t("fields.gameDirectory")} value={values.gameDirectory} />
        <Summary label={t("fields.gamePort")} value={String(values.gamePort)} />
        <Summary label={t("fields.serverName")} value={values.serverName} />
        <Summary label={t("fields.defaultMap")} value={values.defaultMap} />
        <Summary label={t("fields.maxPlayers")} value={String(values.maxPlayers)} />
        <Summary label={t("fields.gameMode")} value={values.gameMode} />
        <Summary label={t("fields.gameType")} value={values.gameType} />
        <Summary label={t("fields.sessionManager")} value={values.sessionManager ?? "tmux"} />
        <Summary label={t("clone.description")} value={values.description || t("clone.unset")} />
      </dl>
      <div className="rounded-md border border-line bg-canvas/60 px-3 py-3">
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-subtle">{t("clone.copiedSettings")}</p>
        <ul className="space-y-1 text-fg-muted">
          <li>{t("clone.aptMirror")}: {template.aptMirror ? `${t("clone.reusedSetting")} (${template.aptMirror})` : t("clone.unset")}</li>
          <li>{t("clone.downloadProxy")}: {template.usePanelProxy ? `${t("clone.reusedSetting")} (${t("clone.panelProxy")})` : template.githubProxy ? `${t("clone.reusedSetting")} (${template.githubProxy})` : `${t("clone.reusedSetting")} (${t("clone.directProxy")})`}</li>
          <li>{t("clone.additionalParameters")}: {values.additionalParameters || t("clone.unset")}</li>
          <li>{t("clone.noFiles")}</li>
        </ul>
      </div>
      <div className="rounded-md border border-line bg-canvas/60 px-3 py-3">
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-subtle">{t("clone.secretsTitle")}</p>
        <ul className="space-y-1 text-fg-muted">
          <li>{t("clone.sshPassword")}: {t("clone.reusedSecret")}</li>
          <li>{t("fields.sudoPassword")}: {secret(values.sudoPassword, template.hasSudoPassword ? t("clone.reusedSecret") : t("clone.sshFallback"))}</li>
          <li>{t("fields.rconPassword")}: {secret(values.rconPassword, t("clone.unset"))}</li>
          <li>{t("fields.steamAccountToken")}: {secret(values.steamAccountToken, t("clone.unset"))}</li>
        </ul>
      </div>
    </div>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-xs text-fg-subtle">{label}</dt><dd className="mt-0.5 break-all font-mono text-sm text-fg">{value}</dd></div>;
}

function Field({ label, htmlFor, children, className, hint }: { label: string; htmlFor: string; children: ReactNode; className?: string; hint?: string }) {
  return <div className={className}><Label htmlFor={htmlFor}>{label}</Label>{children}{hint ? <p className="mt-1 text-xs text-danger">{hint}</p> : null}</div>;
}
