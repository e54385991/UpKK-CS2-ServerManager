"use client";

import {
  AdditionalParametersField,
  OfficialMapField,
} from "@/modules/servers/additional-parameters-field";
import { AptMirrorSwitcher } from "@/modules/servers/apt-mirror-switcher";
import {
  APT_MIRRORS,
  toAptMirror
} from "@/modules/servers/apt-mirrors";
import { GsltTokenField } from "@/modules/servers/gslt-token-field";
import type {
  InitializedHostCredentials
} from "@/modules/servers/setup-api";
import { cn } from "@/shared/lib/cn";
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
import { Check, Copy, Plus, RefreshCw, TriangleAlert } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";

import { Field, GAME_MODES, SummaryItem } from "./create-form-parts";
import { useCreateServerForm } from "./use-create-form";

export function CreateServerForm({
  initialCredentials,
  markedInitializedHost,
}: {
  initialCredentials?: InitializedHostCredentials;
  markedInitializedHost?: string;
} = {}) {
  const {
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
  } = useCreateServerForm({ initialCredentials, markedInitializedHost });

  return (
    <form onSubmit={onSubmit} className="space-y-6">
      {error ? (
        <div className="flex items-start gap-2 rounded-md border border-danger/30 bg-danger-muted/50 px-3 py-2 text-sm text-danger">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      <Card className="border-primary/20 bg-primary/5" data-testid="create-init-gate">
        <CardHeader>
          <div>
            <CardTitle>{t("mustInitializeTitle")}</CardTitle>
            <CardDescription>{t("mustInitializeHelp")}</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-end">
          {hostsLoading ? (
            <p
              className="flex-1 text-sm text-fg-muted"
              data-testid="initialized-hosts-loading"
            >
              {t("loading")}
            </p>
          ) : hosts.length > 0 ? (
            <Field
              className="min-w-0 flex-1"
              label={t("initializedSelect")}
              htmlFor="initializedHost"
            >
              <Select
                id="initializedHost"
                data-testid="initialized-host-select"
                value={selectedHostKey}
                onChange={(event) => void applyInitializedHost(event.target.value)}
              >
                <option value="">{t("initializedSelectPlaceholder")}</option>
                {hosts.map((host) => (
                  <option key={host.key} value={host.key}>
                    {host.name} ({host.sshUser}@{host.host})
                  </option>
                ))}
              </Select>
            </Field>
          ) : (
            <p
              className="flex-1 text-sm text-fg-muted"
              data-testid="initialized-hosts-empty"
            >
              {t("initializedEmpty")}
            </p>
          )}
          <Button
            asChild
            variant={!hostsLoading && hosts.length === 0 ? "primary" : "outline"}
          >
            <Link href={"/servers/new?tab=setup" as Route}>{t("goToSetup")}</Link>
          </Button>
        </CardContent>
      </Card>

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

      {showCreateFields ? (
        <div className="grid gap-6 xl:grid-cols-2">
          <Card>
            <CardHeader>
              <div>
                <CardTitle>{t("connectionTitle")}</CardTitle>
                <CardDescription>{t("connectionHelp")}</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="grid gap-4 sm:grid-cols-2">
              {account ? (
                <div
                  className="sm:col-span-2 space-y-3 rounded-md border border-line bg-canvas/60 px-3 py-3"
                  data-testid="selected-account-summary"
                >
                  <p className="text-xs font-medium uppercase tracking-wide text-fg-subtle">
                    {t("selectedAccountTitle")}
                  </p>
                  <dl className="grid gap-3 sm:grid-cols-2">
                    <SummaryItem label={t("fields.host")} value={account.host} testId="selected-account-host" />
                    <SummaryItem label={t("fields.sshUser")} value={account.sshUser} testId="selected-account-ssh-user" />
                    <SummaryItem label={t("fields.sshPort")} value={String(account.sshPort)} />
                    <Field
                      className="sm:col-span-2"
                      label={t("fields.gameDirectory")}
                      htmlFor="create-game-directory"
                      hint={t("gameDirectoryHelp")}
                    >
                      <Input
                        id="create-game-directory"
                        value={gameDirectory}
                        maxLength={500}
                        spellCheck={false}
                        onChange={(event) => setGameDirectory(event.target.value)}
                      />
                    </Field>
                  </dl>
                  <p className="text-xs text-fg-subtle">{t("selectedAccountHelp")}</p>
                </div>
              ) : (
                <p className="sm:col-span-2 text-sm text-fg-muted">
                  {t("pickAccountRequired")}
                </p>
              )}
              {account && isRoot ? (
                <div
                  className="sm:col-span-2 flex items-start gap-2 rounded-md border border-warn/30 bg-warn-muted/50 px-3 py-2 text-sm text-warn"
                  data-testid="create-root-ssh-warning"
                >
                  <TriangleAlert className="mt-0.5 size-4 shrink-0" />
                  <span>{t("rootSshUserWarning")}</span>
                </div>
              ) : null}
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
              <Field className="sm:col-span-2" label={t("fields.name")} htmlFor="name">
                <Input
                  id="name"
                  name="name"
                  required
                  maxLength={255}
                  autoFocus
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                />
              </Field>
              <Field className="sm:col-span-2" label={t("fields.serverName")} htmlFor="serverName">
                <Input id="serverName" name="serverName" defaultValue="CS2 Server" required />
              </Field>
              <Field label={t("fields.gamePort")} htmlFor="gamePort" hint={t("gamePortHelp")}>
                <Input
                  id="gamePort"
                  name="gamePort"
                  type="number"
                  min={1}
                  max={65534}
                  value={gamePort}
                  onChange={(event) => setGamePort(event.target.value)}
                  required
                />
              </Field>
              <OfficialMapField
                id="defaultMap"
                name="defaultMap"
                defaultValue="de_dust2"
              />
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
              <Field
                label={t("fields.sessionManager")}
                htmlFor="sessionManager"
                hint={t("sessionManagerHelp")}
              >
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
              <GsltTokenField
                className="sm:col-span-2"
                id="steamAccountToken"
                name="steamAccountToken"
                label={t("fields.steamAccountToken")}
                value={steamAccountToken}
                serverName={displayName || undefined}
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
      ) : null}

      {showCreateFields ? (
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
            ) : null}
            <Button type="submit" disabled={pending || !captcha || !canCreate}>
              <Plus />
              {pending ? t("submitting") : t("submit")}
            </Button>
          </CardContent>
        </Card>
      ) : null}
    </form>
  );
}
