"use client";

import { useSyncExternalStore } from "react";
import { DEFAULT_LOCALE, LOCALE_COOKIE, resolveLocale } from "./config";

const subscribe = () => () => {};

function browserLocale() {
  const prefix = `${LOCALE_COOKIE}=`;
  const cookie = document.cookie.split(";").map((value) => value.trim())
    .find((value) => value.startsWith(prefix));
  let locale: string | undefined;
  try {
    locale = cookie ? decodeURIComponent(cookie.slice(prefix.length)) : undefined;
  } catch {
    // A malformed cookie must not break the recovery UI itself.
  }
  return resolveLocale(locale, navigator.languages.join(","));
}

/** Read the client locale after hydration, even if the root provider failed. */
export function useRecoveryLocale() {
  return useSyncExternalStore(subscribe, browserLocale, () => DEFAULT_LOCALE);
}
