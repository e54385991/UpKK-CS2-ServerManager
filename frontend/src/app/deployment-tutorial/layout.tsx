import { getSession } from "@/modules/auth/session";
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
    return <ConsoleShell user={session}>{children}</ConsoleShell>;
  }
  return <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>;
}
