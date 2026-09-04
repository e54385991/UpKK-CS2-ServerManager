"use client";

import { useState, useEffect, useCallback, type FormEvent } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import type { Route } from "next";
import { useTranslations } from "next-intl";
import { RefreshCw, LogIn, TriangleAlert } from "lucide-react";
import { GoogleLoginButton } from "@/modules/auth/google-login";
import { fetchCaptchaChallenge } from "@/shared/lib/captcha";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Input, Label } from "@/shared/ui/input";

type Captcha = { token: string; imageUrl: string; enabled: boolean };
type Translate = ReturnType<typeof useTranslations>;

/**
 * Login form wired to the existing backend auth + CAPTCHA endpoints through the
 * Next API proxy (first-party cookies, no CORS). On success the backend sets
 * the HttpOnly session cookie and we navigate into the console.
 */
export function LoginForm() {
  const t = useTranslations("login");
  const router = useRouter();
  const searchParams = useSearchParams();
  const nextPath = sanitizeNext(searchParams.get("next"));

  const [captcha, setCaptcha] = useState<Captcha | null>(null);
  const [captchaLoading, setCaptchaLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

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
    if (!captcha || (captcha.enabled && !captcha.token)) return;
    setError(null);
    setPending(true);

    const form = new FormData(event.currentTarget);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          username: String(form.get("username") ?? ""),
          password: String(form.get("password") ?? ""),
          ...(captcha.enabled
            ? {
                captcha_token: captcha.token,
                captcha_code: String(form.get("captcha") ?? ""),
              }
            : {}),
        }),
      });

      if (response.ok) {
        router.replace(nextPath);
        router.refresh();
        return;
      }

      setError(await extractDetail(response, t));
      refreshCaptcha();
    } catch {
      setError(t("networkError"));
      refreshCaptcha();
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      {error ? (
        <div className="flex items-center gap-2 rounded-md border border-danger/30 bg-danger-muted/50 px-3 py-2 text-sm text-danger">
          <TriangleAlert className="size-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      <div>
        <Label htmlFor="username">{t("username")}</Label>
        <Input
          id="username"
          name="username"
          autoComplete="username"
          required
          autoFocus
          placeholder="admin"
        />
      </div>

      <div>
        <Label htmlFor="password">{t("password")}</Label>
        <Input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          placeholder="••••••••"
        />
        <Link
          href={"/forgot-password" as Route}
          className="mt-2 inline-block text-xs text-primary hover:underline"
        >
          {t("forgotPassword")}
        </Link>
      </div>

      {captcha?.enabled !== false ? (
        <div>
          <Label htmlFor="captcha">{t("captcha")}</Label>
          <div className="flex items-center gap-3">
            <Input
              id="captcha"
              name="captcha"
              required
              inputMode="text"
              autoComplete="off"
              maxLength={4}
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
                  alt={t("captcha")}
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

      <Button type="submit" size="lg" className="w-full" disabled={pending}>
        <LogIn className="size-4" />
        {pending ? t("submitting") : t("submit")}
      </Button>

      <GoogleLoginButton nextPath={nextPath} />

      <p className="text-center text-sm text-fg-muted">
        {t("noAccount")}{" "}
        <Link href={"/register" as Route} className="text-primary hover:underline">
          {t("registerHere")}
        </Link>
      </p>
    </form>
  );
}

function sanitizeNext(value: string | null): Route {
  if (value && value.startsWith("/") && !value.startsWith("//")) {
    return value as Route;
  }
  return "/overview";
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
  if (response.status === 401) return t("invalidCredentials");
  if (response.status === 400) return t("invalidCaptcha");
  return t("failed", { status: response.status });
}
