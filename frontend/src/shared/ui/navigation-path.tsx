"use client";

import { useEffect, useSyncExternalStore } from "react";
import { usePathname } from "@/i18n/navigation";
import {
  getNavigationPathname,
  publishNavigationPathname,
  subscribeNavigationPathname,
} from "./navigation-path-store";

const serverSnapshot = () => null;

/** One suspended pathname reader keeps all public navigation links mounted. */
export function NavigationPathTracker() {
  const pathname = usePathname();
  useEffect(() => { publishNavigationPathname(pathname); }, [pathname]);
  useEffect(() => () => { publishNavigationPathname(null); }, []);
  return null;
}

export function useNavigationPathname() {
  return useSyncExternalStore(
    subscribeNavigationPathname,
    getNavigationPathname,
    serverSnapshot,
  );
}
