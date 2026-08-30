"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type { Route } from "next";
import { useTranslations } from "next-intl";
import { LoaderCircle, TriangleAlert } from "lucide-react";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Input, Label } from "@/shared/ui/input";

type GoogleConfig = { clientId: string; enabled: boolean };
type Translate = ReturnType<typeof useTranslations>;

const TOKEN_MESSAGE = "google-oauth-token";

export function GoogleLoginButton({ nextPath }: { nextPath: Route }) {
  const t = useTranslations("login");
  const router = useRouter();
  const [config, setConfig] = useState<GoogleConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [idToken, setIdToken] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  useEffect(() => {
    let active = true;
    void fetch("/api/v1/auth/google-config")
      .then(async (response) => {
        if (!response.ok) return null;
        return (await response.json()) as {
          client_id?: unknown;
          enabled?: unknown;
        };
      })
      .then((data) => {
        if (!active) return;
        const clientId = typeof data?.client_id === "string" ? data.client_id : "";
        setConfig({
          clientId,
          enabled: Boolean(data?.enabled && clientId),
        });
      })
      .catch(() => {
        if (active) setConfig({ clientId: "", enabled: false });
      });
    return () => {
      active = false;
    };
  }, []);

  const finishSignIn = useCallback(
    async (token: string, extra?: { username: string; password: string }) => {
      setPending(true);
      setError(null);
      try {
        const response = await fetch("/api/v1/auth/google-oauth", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            id_token: token,
            ...(extra ?? {}),
          }),
        });
        if (response.ok) {
          setIdToken(null);
          router.replace(nextPath);
          router.refresh();
          return;
        }
        const detail = await extractDetail(response, t);
        if (
          response.status === 400 &&
          detail.includes("Username and password required")
        ) {
          setIdToken(token);
          return;
        }
        setError(detail);
      } catch {
        setError(t("networkError"));
      } finally {
        setPending(false);
      }
    },
    [nextPath, router, t],
  );

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin) return;
      const data = event.data as { type?: unknown; id_token?: unknown } | null;
      if (
        !data ||
        data.type !== TOKEN_MESSAGE ||
        typeof data.id_token !== "string" ||
        !data.id_token
      ) {
        return;
      }
      void finishSignIn(data.id_token);
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [finishSignIn]);

  const status = config == null ? "loading" : config.enabled ? "on" : "off";

  function startOAuth() {
    if (!config?.clientId) return;
    const authUrl = new URL("https://accounts.google.com/o/oauth2/v2/auth");
    authUrl.searchParams.set("client_id", config.clientId);
    authUrl.searchParams.set(
      "redirect_uri",
      `${window.location.origin}/google-callback`,
    );
    authUrl.searchParams.set("response_type", "id_token");
    authUrl.searchParams.set("scope", "openid email profile");
    authUrl.searchParams.set("nonce", String(Date.now()));

    const width = 500;
    const height = 600;
    const left = Math.max(0, Math.round(window.screen.width / 2 - width / 2));
    const top = Math.max(0, Math.round(window.screen.height / 2 - height / 2));
    const popup = window.open(
      authUrl.toString(),
      "Google Sign-In",
      `width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes`,
    );
    if (!popup) {
      setError(t("googlePopupBlocked"));
    }
  }

  async function onRegister() {
    if (!idToken) return;
    await finishSignIn(idToken, {
      username: username.trim(),
      password,
    });
  }

  return (
    <div data-google-oauth={status}>
      {config?.enabled ? (
        <div className="space-y-3">
          <div className="flex items-center gap-3 text-xs text-fg-subtle">
            <span className="h-px flex-1 bg-line" />
            {t("googleDivider")}
            <span className="h-px flex-1 bg-line" />
          </div>
          {error ? (
            <div
              role="alert"
              className="flex items-center gap-2 rounded-md border border-danger/30 bg-danger-muted/50 px-3 py-2 text-sm text-danger"
            >
              <TriangleAlert className="size-4 shrink-0" />
              <span>{error}</span>
            </div>
          ) : null}
          <Button
            type="button"
            variant="outline"
            size="lg"
            className="w-full"
            disabled={pending}
            onClick={startOAuth}
          >
            {pending ? <LoaderCircle className="animate-spin" /> : <GoogleMark />}
            {pending ? t("googleSubmitting") : t("googleContinue")}
          </Button>
        </div>
      ) : null}

      <Dialog
        open={idToken != null}
        title={t("googleRegisterTitle")}
        description={t("googleRegisterHelp")}
        closeLabel={t("googleCancel")}
        onClose={() => {
          if (pending) return;
          setIdToken(null);
          setUsername("");
          setPassword("");
        }}
        className="max-w-md"
        footer={
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              disabled={pending}
              onClick={() => {
                setIdToken(null);
                setUsername("");
                setPassword("");
              }}
            >
              {t("googleCancel")}
            </Button>
            <Button
              type="button"
              disabled={pending || username.trim().length < 3 || password.length < 6}
              onClick={() => void onRegister()}
            >
              {pending ? t("googleRegistering") : t("googleRegisterSubmit")}
            </Button>
          </div>
        }
      >
        {error ? (
          <div
            role="alert"
            className="mb-4 flex items-center gap-2 rounded-md border border-danger/30 bg-danger-muted/50 px-3 py-2 text-sm text-danger"
          >
            <TriangleAlert className="size-4 shrink-0" />
            <span>{error}</span>
          </div>
        ) : null}
        <div className="space-y-4">
          <div>
            <Label htmlFor="google-username">{t("username")}</Label>
            <Input
              id="google-username"
              autoComplete="username"
              required
              minLength={3}
              maxLength={100}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
            <p className="mt-1 text-xs text-fg-subtle">{t("googleUsernameHint")}</p>
          </div>
          <div>
            <Label htmlFor="google-password">{t("password")}</Label>
            <Input
              id="google-password"
              type="password"
              autoComplete="new-password"
              required
              minLength={6}
              maxLength={100}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            <p className="mt-1 text-xs text-fg-subtle">{t("googlePasswordHint")}</p>
          </div>
        </div>
      </Dialog>
    </div>
  );
}

function GoogleMark() {
  return (
    <svg viewBox="0 0 18 18" className="size-4" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.83.86-3.04.86-2.34 0-4.32-1.58-5.03-3.71H.96v2.33A9 9 0 0 0 9 18"
      />
      <path
        fill="#FBBC05"
        d="M3.97 10.71A5.41 5.41 0 0 1 3.69 9c0-.59.1-1.17.28-1.71V4.96H.96A9 9 0 0 0 0 9c0 1.45.35 2.82.96 4.04z"
      />
      <path
        fill="#EA4335"
        d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 0 0 .96 4.96l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58"
      />
    </svg>
  );
}

async function extractDetail(
  response: Response,
  t: Translate,
): Promise<string> {
  try {
    const data = (await response.json()) as { detail?: unknown };
    if (typeof data.detail === "string") return data.detail;
  } catch {
    // ignore
  }
  return t("googleFailed", { status: response.status });
}
