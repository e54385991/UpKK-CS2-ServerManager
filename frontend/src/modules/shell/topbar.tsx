import { Suspense } from "react";
import { getTranslations } from "next-intl/server";
import { MobileNav } from "./mobile-nav";
import { LanguageSwitcher } from "./language-switcher";
import { AdminNavigationSlot, UserMenuSlot, ActivityTraySlot, AnnouncementSlot, SshPoolSlot } from "./session-slots";
import { StatusDot } from "@/shared/ui/badge";
import { Skeleton } from "@/shared/ui/skeleton";
import { Menu } from "lucide-react";
import { Button } from "@/shared/ui/button";

export async function Topbar() {
  const t = await getTranslations("shell");
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-canvas/80 px-4 backdrop-blur-md">
      <Suspense fallback={<Button variant="ghost" size="icon" className="md:hidden" disabled aria-label={t("openNav")}><Menu className="size-5" /></Button>}>
        <MobileNav admin={<Suspense fallback={null}><AdminNavigationSlot /></Suspense>} />
      </Suspense>
      <div className="flex items-center gap-3 text-xs text-fg-subtle">
        <span className="inline-flex items-center gap-2">
          <StatusDot tone="ok" pulse />
          <span className="hidden sm:inline">{t("gatewayOnline")}</span>
        </span>
        <Suspense fallback={<Skeleton className="h-4 w-16" />}><SshPoolSlot /></Suspense>
      </div>
      <div className="ml-auto flex items-center gap-2">
        <Suspense fallback={<Skeleton className="size-9" />}><AnnouncementSlot /></Suspense>
        <Suspense fallback={<Skeleton className="size-9" />}><ActivityTraySlot /></Suspense>
        <LanguageSwitcher />
        <Suspense fallback={<Skeleton className="size-9" />}><UserMenuSlot /></Suspense>
      </div>
    </header>
  );
}
