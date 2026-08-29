"use client";

import { useEffect, useRef, useSyncExternalStore } from "react";
import { useTranslations } from "next-intl";

const TOKEN_MESSAGE = "google-oauth-token";

type CallbackSnapshot = {
  readonly idToken: string | null;
  readonly hasOpener: boolean;
};

const SERVER_SNAPSHOT: CallbackSnapshot = Object.freeze({
  idToken: null,
  hasOpener: false,
});

let clientSnapshot: CallbackSnapshot = SERVER_SNAPSHOT;

function subscribeHash(onStoreChange: () => void) {
  window.addEventListener("hashchange", onStoreChange);
  return () => window.removeEventListener("hashchange", onStoreChange);
}

function readHash(): CallbackSnapshot {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const next: CallbackSnapshot = {
    idToken: params.get("id_token"),
    hasOpener: Boolean(window.opener),
  };
  if (
    clientSnapshot.idToken === next.idToken &&
    clientSnapshot.hasOpener === next.hasOpener
  ) {
    return clientSnapshot;
  }
  clientSnapshot = next;
  return clientSnapshot;
}

function readServerHash(): CallbackSnapshot {
  return SERVER_SNAPSHOT;
}

/**
 * Popup landing page for Google's implicit id_token redirect. Posts the token
 * back to the login tab on the same origin, then closes.
 */
export function GoogleCallbackClient() {
  const t = useTranslations("login");
  const posted = useRef(false);
  const snapshot = useSyncExternalStore(subscribeHash, readHash, readServerHash);
  const canPost = Boolean(snapshot.idToken && snapshot.hasOpener);

  useEffect(() => {
    if (!canPost || posted.current || !snapshot.idToken || !window.opener) {
      return;
    }
    posted.current = true;
    window.opener.postMessage(
      { type: TOKEN_MESSAGE, id_token: snapshot.idToken },
      window.location.origin,
    );
    window.close();
  }, [canPost, snapshot.idToken]);

  return (
    <main className="flex min-h-dvh items-center justify-center px-4">
      <p
        className="max-w-sm text-center text-sm text-fg-muted"
        role="status"
        data-google-callback={canPost ? "ok" : "failed"}
        suppressHydrationWarning
      >
        {canPost ? t("googleCallbackWorking") : t("googleCallbackFailed")}
      </p>
    </main>
  );
}
