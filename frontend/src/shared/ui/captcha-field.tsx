"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { fetchCaptchaChallenge } from "@/shared/lib/captcha";
import { Input, Label } from "@/shared/ui/input";
import { cn } from "@/shared/lib/cn";

export type CaptchaValue = { token: string; code: string };

export function useCaptcha() {
  const [token, setToken] = useState("");
  const [imageUrl, setImageUrl] = useState("");
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(true);
  const imageUrlRef = useRef("");

  const applyChallenge = useCallback((next: Awaited<ReturnType<typeof fetchCaptchaChallenge>>) => {
    const previous = imageUrlRef.current;
    if (previous.startsWith("blob:")) URL.revokeObjectURL(previous);
    const nextUrl = next?.imageUrl ?? previous;
    imageUrlRef.current = nextUrl;
    setImageUrl(nextUrl);
    if (next) {
      setToken(next.token);
      setCode("");
    }
    setLoading(false);
  }, []);

  const refresh = useCallback(() => {
    setLoading(true);
    void fetchCaptchaChallenge().then(applyChallenge);
  }, [applyChallenge]);

  useEffect(() => {
    let active = true;
    void fetchCaptchaChallenge().then((next) => {
      if (!active) return;
      applyChallenge(next);
    });
    return () => {
      active = false;
      if (imageUrlRef.current.startsWith("blob:")) {
        URL.revokeObjectURL(imageUrlRef.current);
      }
    };
  }, [applyChallenge]);

  return {
    token,
    code,
    setCode,
    imageUrl,
    loading,
    ready: Boolean(token),
    refresh,
  };
}

export function CaptchaField({
  id,
  label,
  placeholder,
  refreshLabel,
  loadingLabel,
  captcha,
  required = true,
}: {
  id: string;
  label: string;
  placeholder: string;
  refreshLabel: string;
  loadingLabel: string;
  captcha: ReturnType<typeof useCaptcha>;
  required?: boolean;
}) {
  return (
    <div>
      <Label htmlFor={id}>{label}</Label>
      <div className="flex items-center gap-3">
        <Input
          id={id}
          name={id}
          required={required}
          maxLength={4}
          autoComplete="off"
          value={captcha.code}
          onChange={(event) => captcha.setCode(event.target.value)}
          className="uppercase tracking-[0.3em]"
          placeholder={placeholder}
        />
        <button
          type="button"
          onClick={captcha.refresh}
          aria-label={refreshLabel}
          className="relative flex h-10 w-28 shrink-0 items-center justify-center overflow-hidden rounded-md border border-line bg-surface"
        >
          {captcha.imageUrl && !captcha.loading ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={captcha.imageUrl}
              alt={label}
              className="h-full w-full object-contain"
            />
          ) : (
            <span className="text-xs text-fg-subtle">{loadingLabel}</span>
          )}
          <span className="absolute right-1 top-1 rounded bg-canvas/70 p-0.5 text-fg-subtle">
            <RefreshCw className={cn("size-3", captcha.loading && "animate-spin")} />
          </span>
        </button>
      </div>
    </div>
  );
}
