"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { Languages, Check } from "lucide-react";
import { LOCALES, LOCALE_LABELS, LOCALE_COOKIE, type Locale } from "@/i18n/config";
import { setCookie } from "@/shared/lib/cookie";
import { cn } from "@/shared/lib/cn";

/**
 * Locale switcher. Persists the choice in a first-party cookie (SSR-authoritative
 * on the next render) and refreshes the route so server components re-render in
 * the new language without a full reload.
 */
export function LanguageSwitcher() {
  const active = useLocale();
  const router = useRouter();
  const t = useTranslations("shell");
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClick(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  function choose(locale: Locale) {
    setCookie(LOCALE_COOKIE, locale);
    setOpen(false);
    router.refresh();
  }

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t("language")}
        title={t("language")}
        className="flex size-9 items-center justify-center rounded-md border border-line bg-surface text-fg-muted transition-colors hover:bg-surface-overlay hover:text-fg"
      >
        <Languages className="size-4" />
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 top-full z-40 mt-2 w-40 overflow-hidden rounded-lg border border-line bg-surface-overlay shadow-panel"
        >
          {LOCALES.map((locale) => (
            <button
              key={locale}
              role="menuitemradio"
              aria-checked={locale === active}
              type="button"
              onClick={() => choose(locale)}
              className={cn(
                "flex w-full items-center justify-between px-3 py-2 text-sm transition-colors hover:bg-surface-raised",
                locale === active ? "text-fg" : "text-fg-muted",
              )}
            >
              {LOCALE_LABELS[locale]}
              {locale === active ? (
                <Check className="size-4 text-primary" />
              ) : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
