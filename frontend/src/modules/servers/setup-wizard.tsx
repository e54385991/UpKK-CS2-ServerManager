"use client";

import {
  useCallback,
  useEffect,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import Link from "next/link";
import type { Route } from "next";
import { useTranslations } from "next-intl";
import { Check, Copy, RefreshCw, TriangleAlert } from "lucide-react";
import {
  deleteInitializedHostAction,
  getManualSetupScriptAction,
  listInitializedHostsAction,
  runAutoSetupAction,
} from "@/modules/servers/setup-actions";
import type {
  AutoSetupResult,
  InitializedHost,
  ManualSetupScript,
} from "@/modules/servers/setup-api";
import { fetchCaptchaChallenge } from "@/shared/lib/captcha";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Input, Label } from "@/shared/ui/input";
import { cn } from "@/shared/lib/cn";

type Captcha = { token: string; imageUrl: string };
type Mode = "auto" | "manual";

export function SetupWizard() {
  const t = useTranslations("setupWizard");
  const [mode, setMode] = useState<Mode>("auto");
  const [hosts, setHosts] = useState<InitializedHost[]>([]);
  const [captcha, setCaptcha] = useState<Captcha | null>(null);
  const [captchaLoading, setCaptchaLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AutoSetupResult | null>(null);
  const [manual, setManual] = useState<ManualSetupScript | null>(null);
  const [cs2Username, setCs2Username] = useState("cs2server");
  const [copied, setCopied] = useState<string | null>(null);
  const [differentSudo, setDifferentSudo] = useState(false);

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
    if (!/^[a-z_][a-z0-9_-]*$/.test(cs2Username)) return;
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
    try {
      await navigator.clipboard.writeText(value);
      setCopied(id);
    } catch {
      setCopied(null);
    }
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!captcha) return;
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    setResult(null);
    const submitted = await runAutoSetupAction({
      name: String(form.get("name") ?? "").trim(),
      host: String(form.get("host") ?? "").trim(),
      sshPort: Number(form.get("sshPort") ?? 22),
      sshUser: String(form.get("sshUser") ?? "").trim(),
      sshPassword: String(form.get("sshPassword") ?? ""),
      sudoPassword: differentSudo
        ? String(form.get("sudoPassword") ?? "") || undefined
        : undefined,
      cs2Username: String(form.get("cs2Username") ?? "cs2server").trim(),
      captchaToken: captcha.token,
      captchaCode: String(form.get("captcha") ?? "").trim(),
      saveConfig: form.get("saveConfig") === "on",
      openGamePorts: form.get("openGamePorts") === "on",
    });
    setPending(false);
    if (!submitted.ok) {
      setError(submitted.error);
      refreshCaptcha();
      return;
    }
    setResult(submitted.data);
    refreshCaptcha();
    const listed = await listInitializedHostsAction();
    if (listed.ok) setHosts(listed.data);
  }

  return (
    <div className="space-y-6" data-testid="setup-wizard">
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
        <form onSubmit={(event) => void onSubmit(event)} className="space-y-6">
          <Card>
            <CardHeader>
              <div>
                <CardTitle>{t("autoFormTitle")}</CardTitle>
                <CardDescription>{t("autoFormHelp")}</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="grid gap-4 sm:grid-cols-2">
              <Field label={t("fields.name")} htmlFor="setup-name">
                <Input id="setup-name" name="name" required maxLength={255} />
              </Field>
              <Field label={t("fields.host")} htmlFor="setup-host">
                <Input id="setup-host" name="host" required autoComplete="off" />
              </Field>
              <Field label={t("fields.sshPort")} htmlFor="setup-ssh-port">
                <Input
                  id="setup-ssh-port"
                  name="sshPort"
                  type="number"
                  min={1}
                  max={65535}
                  defaultValue={22}
                  required
                />
              </Field>
              <Field label={t("fields.sshUser")} htmlFor="setup-ssh-user">
                <Input id="setup-ssh-user" name="sshUser" required autoComplete="off" />
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
                  pattern="[a-z_][a-z0-9_-]*"
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
                <input type="checkbox" name="saveConfig" defaultChecked />
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
              <Button type="submit" disabled={pending || !captcha}>
                {pending ? t("submitting") : t("submit")}
              </Button>
            </CardContent>
          </Card>
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
                pattern="[a-z_][a-z0-9_-]*"
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
              <Link href={"/servers/new" as Route}>{t("addServerNow")}</Link>
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
            {result.logs.length > 0 ? (
              <pre className="max-h-60 overflow-auto rounded-md border border-line bg-canvas px-3 py-2 font-mono text-xs">
                {result.logs.join("\n")}
              </pre>
            ) : null}
            <Button asChild>
              <Link href={"/servers/new" as Route}>{t("addServerNow")}</Link>
            </Button>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: ReactNode;
}) {
  return (
    <div>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  );
}
