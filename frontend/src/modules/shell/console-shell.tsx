import { Suspense } from "react";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";
import { AdminNavigationSlot } from "./session-slots";

export function ConsoleShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 overflow-hidden" data-testid="console-shell">
      <Sidebar admin={<Suspense fallback={null}><AdminNavigationSlot /></Suspense>} />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Topbar />
        <main className="min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-6 lg:px-8">
          <div className="mx-auto w-full max-w-7xl">{children}</div>
        </main>
      </div>
    </div>
  );
}
