"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Save, TriangleAlert } from "lucide-react";
import {
  applySystemDefaultsAction,
  updateServerAction,
} from "@/modules/servers/actions";
import type { ServerDetail } from "@/modules/servers/api";
import { APT_MIRRORS, toAptMirror } from "@/modules/servers/apt-mirrors";
import { serverProxyMode, type ServerProxyMode } from "@/modules/servers/types";
import { workspaceHref } from "@/modules/servers/workspace";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Input, Label } from "@/shared/ui/input";
import { LinkButton } from "@/shared/ui/link-button";
import { Select } from "@/shared/ui/select";
import { Textarea } from "@/shared/ui/textarea";
import { cn } from "@/shared/lib/cn";

const GAME_MODES = [
  "competitive",
  "casual",
  "wingman",
  "deathmatch",
  "armsrace",
  "demolition",
  "custom",
] as const;

export type ServerConfigSection = "game" | "host";

export function ServerConfigWorkspace({
  server,
  section,
}: {
  server: ServerDetail;
  section: ServerConfigSection;
}) {
  return (
    <>
      <ServerConfigTabs serverId={server.id} section={section} />
      {section === "game" ? (
        <GameConfigForm server={server} />
      ) : (
        <HostConfigForm server={server} />
      )}
    </>
  );
}

function ServerConfigTabs({
  serverId,
  section,
}: {
  serverId: number;
  section: ServerConfigSection;
}) {
  const t = useTranslations("serverWorkspace");
  return (
    <div className="mb-6 flex flex-wrap gap-2" data-testid="server-config-tabs">
      <LinkButton
        href={workspaceHref(serverId, "config")}
        variant={section === "game" ? "primary" : "outline"}
        className={cn(section === "game" && "pointer-events-none")}
      >
        {t("categories.config")}
      </LinkButton>
      <LinkButton
        href={workspaceHref(serverId, "host-config")}
        variant={section === "host" ? "primary" : "outline"}
        className={cn(section === "host" && "pointer-events-none")}
      >
        {t("categories.host-config")}
      </LinkButton>
    </div>
  );
}

function GameConfigForm({ server }: { server: ServerDetail }) {
  const t = useTranslations("serverConfig");
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const rconPassword = String(form.get("rconPassword") ?? "").trim();
    const steamAccountToken = String(form.get("steamAccountToken") ?? "").trim();
    setPending(true);
    setError(null);
    setNotice(null);
    const result = await updateServerAction(server.id, {
      name: String(form.get("name") ?? "").trim(),
      gamePort: Number(form.get("gamePort")),
      description: String(form.get("description") ?? "").trim() || null,
      serverName: String(form.get("serverName") ?? "").trim(),
      defaultMap: String(form.get("defaultMap") ?? "").trim(),
      maxPlayers: Number(form.get("maxPlayers")),
      gameMode: String(form.get("gameMode") ?? "").trim(),
      gameType: String(form.get("gameType") ?? "").trim(),
      rconPassword: rconPassword || undefined,
      steamAccountToken: steamAccountToken || undefined,
    });
    setPending(false);
    if (!result.ok) {
      setError(result.error || t("failed"));
      return;
    }
    setNotice(result.data.restartRequired ? t("restartRequired") : t("saved"));
    router.refresh();
  }

  return (
    <form
      onSubmit={(event) => void onSubmit(event)}
      className="max-w-3xl space-y-6"
      data-testid="game-config-form"
    >
      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("title")}</CardTitle>
            <CardDescription>{t("help")}</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          <Section title={t("identity")}>
            <Field label={t("fields.name")} htmlFor="name">
              <Input id="name" name="name" required defaultValue={server.name} />
            </Field>
            <Field label={t("fields.gamePort")} htmlFor="gamePort">
              <Input
                id="gamePort"
                name="gamePort"
                type="number"
                min={1}
                max={65535}
                required
                defaultValue={server.gamePort}
              />
            </Field>
            <Field label={t("fields.description")} htmlFor="description">
              <Textarea
                id="description"
                name="description"
                rows={3}
                defaultValue={server.description ?? ""}
              />
            </Field>
          </Section>

          <Section title={t("game")}>
            <Field label={t("fields.serverName")} htmlFor="serverName">
              <Input
                id="serverName"
                name="serverName"
                required
                defaultValue={server.serverName}
              />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t("fields.defaultMap")} htmlFor="defaultMap">
                <Input
                  id="defaultMap"
                  name="defaultMap"
                  required
                  defaultValue={server.defaultMap}
                />
              </Field>
              <Field label={t("fields.maxPlayers")} htmlFor="maxPlayers">
                <Input
                  id="maxPlayers"
                  name="maxPlayers"
                  type="number"
                  min={1}
                  max={64}
                  required
                  defaultValue={server.maxPlayers}
                />
              </Field>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t("fields.gameMode")} htmlFor="gameMode">
                <Select id="gameMode" name="gameMode" defaultValue={server.gameMode}>
                  {GAME_MODES.map((mode) => (
                    <option key={mode} value={mode}>
                      {mode}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label={t("fields.gameType")} htmlFor="gameType">
                <Input id="gameType" name="gameType" required defaultValue={server.gameType} />
              </Field>
            </div>
          </Section>

          <Section title={t("secrets")} description={t("secretsHelp")}>
            <Field label={t("fields.rconPassword")} htmlFor="rconPassword">
              <Input id="rconPassword" name="rconPassword" type="password" autoComplete="new-password" />
            </Field>
            <Field label={t("fields.steamAccountToken")} htmlFor="steamAccountToken">
              <Input
                id="steamAccountToken"
                name="steamAccountToken"
                type="password"
                autoComplete="off"
              />
            </Field>
          </Section>

          <FormStatus error={error} notice={notice} />
          <Button type="submit" disabled={pending}>
            <Save />
            {pending ? t("saving") : t("save")}
          </Button>
        </CardContent>
      </Card>
    </form>
  );
}

function HostConfigForm({ server }: { server: ServerDetail }) {
  const t = useTranslations("serverConfig");
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [proxyMode, setProxyMode] = useState<ServerProxyMode>(
    serverProxyMode(server),
  );
  const [githubProxy, setGithubProxy] = useState(server.githubProxy ?? "");
  const isRoot = server.sshUser.trim().toLowerCase() === "root";

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const sshPassword = String(form.get("sshPassword") ?? "").trim();
    const sudoPassword = String(form.get("sudoPassword") ?? "").trim();
    setPending(true);
    setError(null);
    setNotice(null);
    const result = await updateServerAction(server.id, {
      host: String(form.get("host") ?? "").trim(),
      sshPort: Number(form.get("sshPort")),
      sshUser: String(form.get("sshUser") ?? "").trim(),
      sshPassword: sshPassword || undefined,
      gameDirectory: String(form.get("gameDirectory") ?? "").trim(),
      sessionManager:
        form.get("sessionManager") === "screen" ? "screen" : "tmux",
      sudoPassword: sudoPassword || undefined,
      aptMirror: toAptMirror(String(form.get("aptMirror") ?? "")) ?? undefined,
      usePanelProxy: proxyMode === "panel",
      githubProxy: proxyMode === "github_url" ? githubProxy.trim() || null : null,
    });
    setPending(false);
    if (!result.ok) {
      setError(result.error || t("failed"));
      return;
    }
    setNotice(result.data.restartRequired ? t("restartRequired") : t("saved"));
    router.refresh();
  }

  async function onApplyDefaults() {
    setPending(true);
    setError(null);
    setNotice(null);
    const result = await applySystemDefaultsAction(server.id);
    setPending(false);
    if (!result.ok) {
      setError(result.error || t("applyDefaultsFailed"));
      return;
    }
    setProxyMode(serverProxyMode(result.data));
    setGithubProxy(result.data.githubProxy ?? "");
    setNotice(t("applyDefaultsOk"));
    router.refresh();
  }

  return (
    <form
      onSubmit={(event) => void onSubmit(event)}
      className="max-w-3xl space-y-6"
      data-testid="host-config-form"
    >
      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("hostTitle")}</CardTitle>
            <CardDescription>{t("hostHelp")}</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          <Section title={t("linux")} description={t("linuxHelp")}>
            <Field label={t("fields.host")} htmlFor="host">
              <Input id="host" name="host" required defaultValue={server.host} />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t("fields.sshPort")} htmlFor="sshPort">
                <Input
                  id="sshPort"
                  name="sshPort"
                  type="number"
                  min={1}
                  max={65535}
                  required
                  defaultValue={server.sshPort}
                />
              </Field>
              <Field
                label={t("fields.sshUser")}
                htmlFor="sshUser"
                hint={t("sshUserHelp")}
              >
                <Input id="sshUser" name="sshUser" required defaultValue={server.sshUser} />
              </Field>
            </div>
            {isRoot ? (
              <div className="flex items-start gap-2 rounded-md border border-warn/30 bg-warn-muted/50 px-3 py-2 text-sm text-warn">
                <TriangleAlert className="mt-0.5 size-4 shrink-0" />
                <span>{t("rootSshUserWarning")}</span>
              </div>
            ) : null}
            <Field label={t("fields.gameDirectory")} htmlFor="gameDirectory">
              <Input
                id="gameDirectory"
                name="gameDirectory"
                required
                defaultValue={server.gameDirectory}
              />
            </Field>
            <Field label={t("fields.sessionManager")} htmlFor="sessionManager">
              <Select
                id="sessionManager"
                name="sessionManager"
                defaultValue={server.sessionManager}
              >
                <option value="tmux">tmux</option>
                <option value="screen">screen</option>
              </Select>
            </Field>
            <Field
              label={t("fields.aptMirror")}
              htmlFor="aptMirror"
              hint={t("aptMirrorHelp")}
            >
              <Select
                id="aptMirror"
                name="aptMirror"
                defaultValue={toAptMirror(server.aptMirror) ?? "official"}
              >
                {APT_MIRRORS.map((mirror) => (
                  <option key={mirror} value={mirror}>
                    {t(`mirrors.${mirror}`)}
                  </option>
                ))}
              </Select>
            </Field>
            <p className="text-xs text-fg-subtle">
              {server.hasSudoPassword ? t("hasSudoPassword") : t("missingSudoPassword")}
            </p>
            <Field
              label={t("fields.sudoPassword")}
              htmlFor="sudoPassword"
              hint={t("sudoPasswordHelp")}
            >
              <Input
                id="sudoPassword"
                name="sudoPassword"
                type="password"
                autoComplete="new-password"
              />
            </Field>
            <Field
              label={t("fields.sshPassword")}
              htmlFor="sshPassword"
              hint={isRoot ? t("sshPasswordRootHelp") : t("sshPasswordHelp")}
            >
              <Input id="sshPassword" name="sshPassword" type="password" autoComplete="new-password" />
            </Field>
          </Section>

          <Section title={t("proxy")} description={t("proxyHelp")}>
            <p className="text-xs text-fg-muted">
              <span className="font-medium text-fg">{t("currentProxy")}: </span>
              {t(`proxyMode.${serverProxyMode(server)}`)}
              {server.githubProxy ? ` · ${server.githubProxy}` : ""}
            </p>
            <fieldset className="space-y-2">
              <legend className="sr-only">{t("proxy")}</legend>
              {(["panel", "github_url", "direct"] as const).map((mode) => (
                <label key={mode} className="flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name="proxyMode"
                    checked={proxyMode === mode}
                    onChange={() => setProxyMode(mode)}
                  />
                  {t(`proxyMode.${mode}`)}
                </label>
              ))}
            </fieldset>
            {proxyMode === "github_url" ? (
              <Field
                label={t("fields.githubProxy")}
                htmlFor="githubProxy"
                hint={t("githubProxyHelp")}
              >
                <Input
                  id="githubProxy"
                  value={githubProxy}
                  placeholder="https://ghfast.top"
                  onChange={(event) => setGithubProxy(event.target.value)}
                />
              </Field>
            ) : null}
            <Button
              type="button"
              variant="outline"
              disabled={pending}
              data-testid="apply-system-defaults"
              onClick={() => void onApplyDefaults()}
            >
              {pending ? t("applyingDefaults") : t("applyDefaults")}
            </Button>
          </Section>

          <FormStatus error={error} notice={notice} />
          <Button type="submit" disabled={pending}>
            <Save />
            {pending ? t("saving") : t("saveHost")}
          </Button>
        </CardContent>
      </Card>
    </form>
  );
}

function FormStatus({
  error,
  notice,
}: {
  error: string | null;
  notice: string | null;
}) {
  return (
    <>
      {error ? (
        <p className="flex items-center gap-2 text-sm text-danger">
          <TriangleAlert className="size-4" />
          {error}
        </p>
      ) : null}
      {notice ? <p className="text-sm text-ok">{notice}</p> : null}
    </>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold text-fg">{title}</h3>
        {description ? (
          <p className="text-xs text-fg-subtle">{description}</p>
        ) : null}
      </div>
      <div className="space-y-3">{children}</div>
    </div>
  );
}

function Field({
  label,
  htmlFor,
  children,
  hint,
}: {
  label: string;
  htmlFor: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint ? <p className="text-xs text-fg-subtle">{hint}</p> : null}
    </div>
  );
}
