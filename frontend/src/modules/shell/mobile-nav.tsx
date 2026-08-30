"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu, X, Crosshair } from "lucide-react";
import { useTranslations } from "next-intl";
import { NAV_SECTIONS } from "@/shared/config/navigation";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";

/**
 * Hamburger + overlay drawer for viewports below `md`.
 *
 * The overlay is portaled to `document.body`. The trigger lives in the sticky
 * topbar, which uses `backdrop-blur`; that filter makes `position: fixed`
 * descendants resolve against the header instead of the viewport, so an in-tree
 * drawer overflowed and covered the page.
 *
 * Open state is tied to the current pathname so a navigation automatically
 * dismisses the drawer without an effect.
 */
export function MobileNav({ isAdmin }: { isAdmin: boolean }) {
  const pathname = usePathname();
  const [openPath, setOpenPath] = useState<string | null>(null);
  const open = openPath === pathname;
  const t = useTranslations("nav");
  const tSite = useTranslations("site");
  const tShell = useTranslations("shell");

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenPath(null);
    };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const overlay = open ? (
    <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal>
      <div
        className="absolute inset-0 bg-canvas/80 backdrop-blur-sm"
        onClick={() => setOpenPath(null)}
      />
      <div className="absolute inset-y-0 left-0 flex w-72 flex-col border-r border-line bg-surface shadow-panel">
        <div className="flex h-14 items-center justify-between border-b border-line px-5">
          <span className="flex items-center gap-2.5">
            <span className="flex size-8 items-center justify-center rounded-md bg-primary-muted text-primary ring-1 ring-primary/30">
              <Crosshair className="size-4.5" />
            </span>
            <span className="min-w-0 text-[12px] font-semibold leading-snug text-fg">
              {tSite("name")}
            </span>
          </span>
          <Button
            variant="ghost"
            size="icon"
            aria-label={tShell("closeNav")}
            onClick={() => setOpenPath(null)}
          >
            <X className="size-5" />
          </Button>
        </div>
        <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-4">
          {NAV_SECTIONS.map((section) => {
            const items = section.items.filter(
              (item) => !item.adminOnly || isAdmin,
            );
            if (items.length === 0) return null;
            return (
              <div key={section.titleKey}>
                <p className="px-3 pb-2 text-[0.6875rem] font-semibold uppercase tracking-[0.14em] text-fg-subtle">
                  {t(section.titleKey)}
                </p>
                <div className="space-y-1">
                  {items.map((item) => {
                    const active =
                      pathname === item.href ||
                      pathname.startsWith(`${item.href}/`);
                    return (
                      <Link
                        key={item.href}
                        href={item.href}
                        onClick={() => setOpenPath(null)}
                        className={cn(
                          "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium",
                          active
                            ? "bg-surface-overlay text-fg"
                            : "text-fg-muted hover:bg-surface-overlay/60 hover:text-fg",
                        )}
                      >
                        <item.icon
                          className={cn(
                            "size-4",
                            active ? "text-primary" : "text-fg-subtle",
                          )}
                        />
                        {t(item.key)}
                      </Link>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </nav>
      </div>
    </div>
  ) : null;

  return (
    <>
      <Button
        variant="ghost"
        size="icon"
        className="md:hidden"
        aria-label={tShell("openNav")}
        aria-expanded={open}
        onClick={() => setOpenPath(pathname)}
      >
        <Menu className="size-5" />
      </Button>
      {overlay ? createPortal(overlay, document.body) : null}
    </>
  );
}