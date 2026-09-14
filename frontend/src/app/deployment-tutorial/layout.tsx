import { getSession } from "@/modules/auth/render-session";
import { ClientMessages } from "@/i18n/client-messages";
import { CONSOLE_NAMESPACES } from "@/i18n/namespaces";
import { ConsoleShell } from "@/modules/shell/console-shell";

/**
 * Keep the tutorial readable without a session, but restore the console
 * sidebar/topbar when the visitor is already signed in (overview → tutorial
 * used to drop the chrome and trap scroll in the root overflow-hidden frame).
 */
export default async function DeploymentTutorialLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await getSession();
  if (session) {
    return (
      <ClientMessages namespaces={CONSOLE_NAMESPACES}>
        <ConsoleShell user={session}>{children}</ConsoleShell>
      </ClientMessages>
    );
  }
  return <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>;
}
