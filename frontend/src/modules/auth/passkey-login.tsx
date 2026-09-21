"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { Route } from "next";
import { useTranslations } from "next-intl";
import { Fingerprint, LoaderCircle, TriangleAlert } from "lucide-react";
import { Button } from "@/shared/ui/button";
import {
  credentialToJson,
  extractPasskeyDetail,
  isPasskeyErrorCode,
  isPasskeySupported,
  passkeyEnvironmentIssue,
  requestOptionsFromJson,
} from "@/modules/auth/webauthn";

type JsonRecord = Record<string, unknown>;

export function PasskeyLoginButton({
  nextPath,
  username,
}: {
  nextPath: Route;
  username: () => string;
}) {
  const t = useTranslations("login");
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const conditionalAbort = useRef<AbortController | null>(null);
  const finishing = useRef(false);

  const mapError = useCallback(
    (detail: string | null, status: number) => {
      if (detail && isPasskeyErrorCode(detail)) return t(detail);
      if (detail === "NotAllowedError") return t("passkeyCancelled");
      return t("passkeyFailed", { status });
    },
    [t],
  );

  const finishAssertion = useCallback(
    async (credential: PublicKeyCredential) => {
      if (finishing.current) return;
      finishing.current = true;
      setPending(true);
      setError(null);
      try {
        const response = await fetch("/api/v1/auth/passkeys/login/verify", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ credential: credentialToJson(credential) }),
        });
        if (response.ok) {
          router.replace(nextPath);
          router.refresh();
          return;
        }
        setError(mapError(await extractPasskeyDetail(response), response.status));
      } catch {
        setError(t("networkError"));
      } finally {
        finishing.current = false;
        setPending(false);
      }
    },
    [mapError, nextPath, router, t],
  );

  useEffect(() => {
    if (!isPasskeySupported() || passkeyEnvironmentIssue() !== null) return;
    const isConditional = (
      PublicKeyCredential as typeof PublicKeyCredential & {
        isConditionalMediationAvailable?: () => Promise<boolean>;
      }
    ).isConditionalMediationAvailable;
    if (typeof isConditional !== "function") return;

    let cancelled = false;
    const abort = new AbortController();
    conditionalAbort.current = abort;
    void isConditional()
      .then(async (available) => {
        if (!available || cancelled) return;
        const optionsResponse = await fetch("/api/v1/auth/passkeys/login/options", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: "{}",
        });
        if (!optionsResponse.ok || cancelled) return;
        const payload = (await optionsResponse.json()) as { public_key?: JsonRecord };
        if (!payload.public_key) return;
        const credential = await navigator.credentials.get({
          publicKey: requestOptionsFromJson(payload.public_key),
          mediation: "conditional",
          signal: abort.signal,
        });
        if (credential instanceof PublicKeyCredential) {
          await finishAssertion(credential);
        }
      })
      .catch((cause: unknown) => {
        if (abort.signal.aborted || cancelled) return;
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        if (cause instanceof DOMException && cause.name === "NotAllowedError") return;
      });
    return () => {
      cancelled = true;
      abort.abort();
      if (conditionalAbort.current === abort) conditionalAbort.current = null;
    };
  }, [finishAssertion]);

  async function onPasskeyClick() {
    const env = passkeyEnvironmentIssue();
    if (env === "ip") {
      setError(t("passkey_ip_origin"));
      return;
    }
    if (env === "insecure") {
      setError(t("passkey_insecure_origin"));
      return;
    }
    if (!isPasskeySupported()) {
      setError(t("passkeyUnavailable"));
      return;
    }
    conditionalAbort.current?.abort();
    setPending(true);
    setError(null);
    try {
      const optionsResponse = await fetch("/api/v1/auth/passkeys/login/options", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ username: username().trim() || null }),
      });
      if (!optionsResponse.ok) {
        setError(mapError(await extractPasskeyDetail(optionsResponse), optionsResponse.status));
        return;
      }
      const payload = (await optionsResponse.json()) as { public_key?: JsonRecord };
      if (!payload.public_key) {
        setError(t("passkeyFailed", { status: optionsResponse.status }));
        return;
      }
      const credential = await navigator.credentials.get({
        publicKey: requestOptionsFromJson(payload.public_key),
      });
      if (!(credential instanceof PublicKeyCredential)) {
        setError(t("passkeyCancelled"));
        return;
      }
      await finishAssertion(credential);
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "NotAllowedError") {
        setError(t("passkeyCancelled"));
      } else if (cause instanceof DOMException && cause.name === "AbortError") {
        setError(t("passkeyCancelled"));
      } else {
        setError(t("networkError"));
      }
    } finally {
      setPending(false);
    }
  }

  return (
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
        onClick={() => void onPasskeyClick()}
      >
        {pending ? <LoaderCircle className="animate-spin" /> : <Fingerprint />}
        {pending ? t("passkeySubmitting") : t("passkeyContinue")}
      </Button>
    </div>
  );
}
