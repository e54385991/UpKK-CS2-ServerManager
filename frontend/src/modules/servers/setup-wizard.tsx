"use client";

import {
CS2_USERNAME_PATTERN
} from "@/modules/servers/cs2-username";
import {
addServerAfterSetupHref
} from "@/modules/servers/initialized-hosts";
import { SetupLiveLog } from "@/modules/servers/setup-live-log";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import {
Card,
CardContent,
CardDescription,
CardHeader,
CardTitle,
} from "@/shared/ui/card";
import { Input,Label } from "@/shared/ui/input";
import { Check,Copy,RefreshCw,TriangleAlert } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";

import { Field } from "./setup-wizard-parts";
import { useSetupWizard } from "./use-setup-wizard";

export function SetupWizard({
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
  const {
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
  } = useSetupWizard({ initialName, initialHost, initialSshPort, initialSshUser, requireInit });

  return (
    <div className="space-y-6" data-testid="setup-wizard">
      {requireInit ? (
        <div
          className="rounded-md border border-warn/30 bg-warn-muted/50 px-3 py-2 text-sm text-fg"
          data-testid="setup-must-initialize"
        >
          <p className="font-medium">{t("mustInitializeTitle")}</p>
          <p className="mt-1 text-fg-muted">{t("mustInitialize")}</p>
        </div>
      ) : null}
      {error ? (
        <div className="flex items-start gap-2 rounded-md border border-danger/30 bg-danger-muted/50 px-3 py-2 text-sm text-danger">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      {hosts.length > 0 ? (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>{t("savedTitle")}</CardTitle>
              <CardDescription>{t("savedHelp")}</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            {hosts.map((host) => (
              <div
                key={host.key}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-line px-3 py-2 text-sm"
              >
                <div>
                  <p className="font-medium text-fg">{host.name}</p>
                  <p className="font-mono text-xs text-fg-muted">
                    {host.sshUser}@{host.host}:{host.sshPort} · {host.gameDirectory}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => void onDelete(host.key)}
                >
                  {t("deleteSaved")}
                </Button>
              </div>
            ))}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("infoTitle")}</CardTitle>
            <CardDescription>{t("infoHelp")}</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="text-sm text-fg-muted">
          <p>{t("testedEnvironment")}</p>
        </CardContent>
      </Card>

      <div className="grid gap-3 sm:grid-cols-2">
        <button
          type="button"
          data-testid="setup-mode-auto"
          onClick={() => setMode("auto")}
          className={cn(
            "rounded-lg border px-4 py-3 text-left",
            mode === "auto"
              ? "border-primary bg-primary/5"
              : "border-line bg-surface",
          )}
        >
          <p className="text-sm font-medium text-fg">{t("autoTitle")}</p>
          <p className="mt-1 text-xs text-fg-muted">{t("autoHelp")}</p>
        </button>
        <button
          type="button"
          data-testid="setup-mode-manual"
          onClick={() => setMode("manual")}
          className={cn(
            "rounded-lg border px-4 py-3 text-left",
            mode === "manual"
              ? "border-primary bg-primary/5"
              : "border-line bg-surface",
          )}
        >
          <p className="text-sm font-medium text-fg">{t("manualTitle")}</p>
          <p className="mt-1 text-xs text-fg-muted">{t("manualHelp")}</p>
        </button>
      </div>

      {mode === "auto" ? (
        <form
          ref={setupFormRef}
          onSubmit={(event) => void onSubmit(event)}
          className="space-y-6"
        >
          <Card>
            <CardHeader>
              <div>
                <CardTitle>{t("autoFormTitle")}</CardTitle>
                <CardDescription>{t("autoFormHelp")}</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="grid gap-4 sm:grid-cols-2">
              <Field label={t("fields.name")} htmlFor="setup-name">
                <Input
                  id="setup-name"
                  name="name"
                  required
                  maxLength={255}
                  defaultValue={initialName}
                />
              </Field>
              <Field label={t("fields.host")} htmlFor="setup-host">
                <Input
                  id="setup-host"
                  name="host"
                  required
                  autoComplete="off"
                  defaultValue={initialHost}
                />
              </Field>
              <Field label={t("fields.sshPort")} htmlFor="setup-ssh-port">
                <Input
                  id="setup-ssh-port"
                  name="sshPort"
                  type="number"
                  min={1}
                  max={65535}
                  defaultValue={initialSshPort}
                  required
                />
              </Field>
              <Field label={t("fields.sshUser")} htmlFor="setup-ssh-user">
                <Input
                  id="setup-ssh-user"
                  name="sshUser"
                  required
                  autoComplete="off"
                  defaultValue={initialSshUser}
                />
              </Field>
              <Field label={t("fields.sshPassword")} htmlFor="setup-ssh-password">
                <Input
                  id="setup-ssh-password"
                  name="sshPassword"
                  type="password"
                  required
                  autoComplete="new-password"
                />
              </Field>
              <Field label={t("fields.cs2Username")} htmlFor="setup-cs2-user">
                <Input
                  id="setup-cs2-user"
                  name="cs2Username"
                  value={cs2Username}
                  onChange={(event) => setCs2Username(event.target.value)}
                  pattern={CS2_USERNAME_PATTERN}
                  required
                />
              </Field>
              <label className="sm:col-span-2 flex items-center gap-2 text-sm text-fg">
                <input
                  type="checkbox"
                  checked={differentSudo}
                  onChange={(event) => setDifferentSudo(event.target.checked)}
                />
                {t("fields.differentSudo")}
              </label>
              {differentSudo ? (
                <Field label={t("fields.sudoPassword")} htmlFor="setup-sudo">
                  <Input
                    id="setup-sudo"
                    name="sudoPassword"
                    type="password"
                    autoComplete="new-password"
                  />
                </Field>
              ) : null}
              <label className="flex items-center gap-2 text-sm text-fg">
                <input type="checkbox" checked disabled readOnly />
                {t("fields.saveConfig")}
              </label>
              <label className="flex items-center gap-2 text-sm text-fg">
                <input type="checkbox" name="openGamePorts" defaultChecked />
                {t("fields.openGamePorts")}
              </label>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div>
                <CardTitle>{t("confirmTitle")}</CardTitle>
                <CardDescription>{t("confirmHelp")}</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="flex flex-col gap-4 sm:flex-row sm:items-end">
              {captcha?.enabled !== false ? (
                <div className="min-w-0 flex-1">
                  <Label htmlFor="setup-captcha">{t("fields.captcha")}</Label>
                  <div className="flex items-center gap-3">
                    <Input
                      id="setup-captcha"
                      name="captcha"
                      required
                      maxLength={4}
                      autoComplete="off"
                      className="uppercase tracking-[0.3em]"
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
              ) : null}
              <Button type="submit" disabled={pending || !captcha}>
                {pending ? t("submitting") : t("submit")}
              </Button>
            </CardContent>
          </Card>
          {setupFailed ? (
            <Card
              className="border-warn/40 bg-warn-muted/40"
              data-testid="setup-force-add"
            >
              <CardHeader>
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <TriangleAlert className="size-4 text-warn" />
                    {t("forceAddTitle")}
                  </CardTitle>
                  <CardDescription>{t("forceAddHelp")}</CardDescription>
                </div>
              </CardHeader>
              <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-sm text-warn">{t("forceAddWarning")}</p>
                <Button
                  type="button"
                  variant="outline"
                  disabled={pending || captchaLoading || !captcha}
                  onClick={() => void onForceAdd()}
                >
                  {pending ? t("forceAddSubmitting") : t("forceAddButton")}
                </Button>
              </CardContent>
            </Card>
          ) : null}
          <SetupLiveLog logs={logs} pending={pending} />
        </form>
      ) : (
        <Card data-testid="setup-manual-script">
          <CardHeader>
            <div>
              <CardTitle>{t("manualStepsTitle")}</CardTitle>
              <CardDescription>{t("manualStepsHelp")}</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <Field label={t("fields.cs2Username")} htmlFor="manual-cs2-user">
              <Input
                id="manual-cs2-user"
                value={cs2Username}
                onChange={(event) => setCs2Username(event.target.value)}
                pattern={CS2_USERNAME_PATTERN}
              />
            </Field>
            {manual ? (
              <>
                <div>
                  <p className="mb-1 text-xs font-medium uppercase text-fg-subtle">
                    {t("generatedPassword")}
                  </p>
                  <div className="flex items-center gap-2">
                    <code className="flex-1 overflow-x-auto rounded-md border border-line bg-canvas px-3 py-2 text-sm">
                      {manual.password}
                    </code>
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      onClick={() => void copy(manual.password, "password")}
                      aria-label={t("copy")}
                    >
                      {copied === "password" ? <Check /> : <Copy />}
                    </Button>
                  </div>
                </div>
                <div>
                  <p className="mb-1 text-xs font-medium uppercase text-fg-subtle">
                    {t("scriptTitle")}
                  </p>
                  <pre className="max-h-80 overflow-auto rounded-md border border-line bg-canvas px-3 py-2 font-mono text-xs">
                    {manual.script}
                  </pre>
                  <Button
                    type="button"
                    variant="outline"
                    className="mt-2"
                    onClick={() => void copy(manual.script, "script")}
                  >
                    {copied === "script" ? t("copied") : t("copyScript")}
                  </Button>
                </div>
              </>
            ) : (
              <p className="text-sm text-fg-muted">{t("loading")}</p>
            )}
            <Button asChild>
              <Link href={"/servers/new?initialized=1" as Route}>{t("addServerNow")}</Link>
            </Button>
          </CardContent>
        </Card>
      )}

      {result ? (
        <Card className="border-ok/30" data-testid="setup-result">
          <CardHeader>
            <div>
              <CardTitle>{t("completeTitle")}</CardTitle>
              <CardDescription>{t("completeHelp")}</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <p>{result.message}</p>
            <p>
              {t("fields.cs2Username")}: <code>{result.cs2Username}</code>
            </p>
            <p>
              {t("resultPassword")}: <code>{result.cs2Password}</code>
            </p>
            <p>
              {t("fields.gameDirectory")}: <code>{result.gameDirectory}</code>
            </p>
            {logs.length === 0 && result.logs.length > 0 ? (
              <pre className="max-h-60 overflow-auto rounded-md border border-line bg-canvas px-3 py-2 font-mono text-xs">
                {result.logs.join("\n")}
              </pre>
            ) : null}
            <Button asChild>
              <Link
                href={
                  addServerAfterSetupHref({
                    host: completedHost,
                    initializedServerId: result.initializedServerId,
                    sshUser: result.cs2Username,
                  }) as Route
                }
              >
                {t("addServerNow")}
              </Link>
            </Button>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
