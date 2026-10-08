import { Suspense } from "react";
import { getSession } from "@/modules/auth/render-session";
import { ClientMessages } from "@/i18n/client-messages";
import { CONSOLE_NAMESPACES } from "@/i18n/namespaces";
import { Sidebar } from "@/modules/shell/sidebar";
import { Topbar } from "@/modules/shell/topbar";
import { AdminNavigationSlot } from "@/modules/shell/session-slots";

export default function DeploymentTutorialLayout({ children }: { children: React.ReactNode }) {
  return <ClientMessages namespaces={CONSOLE_NAMESPACES}>
    <div className="flex min-h-0 flex-1 overflow-hidden">
      <Suspense fallback={null}><TutorialSidebar /></Suspense>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Suspense fallback={null}><TutorialTopbar /></Suspense>
        <main className="min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-6 lg:px-8">{children}</main>
      </div>
    </div>
  </ClientMessages>;
}
async function TutorialSidebar() {
  return await getSession() ? <Sidebar admin={<Suspense fallback={null}><AdminNavigationSlot /></Suspense>} /> : null;
}
async function TutorialTopbar() {
  return await getSession() ? <Topbar /> : null;
}
