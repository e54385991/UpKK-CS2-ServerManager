"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { LogOut, UserRound, ChevronDown } from "lucide-react";
import { cn } from "@/shared/lib/cn";
import type { SessionUser } from "@/modules/auth/session";

export function UserMenu({ user }: { user: SessionUser }) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const t = useTranslations("shell");

  useEffect(() => {
    function onClick(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  async function signOut() {
    setPending(true);
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } catch {
      // Fall through to redirect regardless; the session cookie may already be
      // gone and the login page will re-challenge if not.
    } finally {
      router.replace("/login");
      router.refresh();
    }
  }

  const initial = user.username.slice(0, 1).toUpperCase();

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex items-center gap-2 rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-fg-muted transition-colors hover:bg-surface-overlay hover:text-fg"
      >
        <span className="flex size-6 items-center justify-center rounded-full bg-primary-muted text-xs font-semibold text-primary">
          {initial}
        </span>
        <span className="hidden max-w-[10rem] truncate sm:inline">
          {user.username}
        </span>
        <ChevronDown className="size-3.5" />
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 top-full z-40 mt-2 w-52 overflow-hidden rounded-lg border border-line bg-surface-overlay shadow-panel"
        >
          <div className="border-b border-line px-3 py-2.5">
            <p className="truncate text-sm font-medium text-fg">
              {user.username}
            </p>
            <p className="truncate text-xs text-fg-subtle">
              {user.isAdmin ? t("admin") : t("user")}
            </p>
          </div>
          <a
            role="menuitem"
            href="/settings/profile"
            className="flex items-center gap-2.5 px-3 py-2 text-sm text-fg-muted transition-colors hover:bg-surface-raised hover:text-fg"
          >
            <UserRound className="size-4" />
            {t("profile")}
          </a>
          <button
            role="menuitem"
            type="button"
            disabled={pending}
            onClick={signOut}
            className={cn(
              "flex w-full items-center gap-2.5 px-3 py-2 text-sm text-danger transition-colors hover:bg-danger-muted disabled:opacity-60",
            )}
          >
            <LogOut className="size-4" />
            {pending ? t("signingOut") : t("logout")}
          </button>
        </div>
      ) : null}
    </div>
  );
}
