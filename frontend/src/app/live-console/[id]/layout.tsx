import { requireSession } from "@/modules/auth/session";

export default async function LiveConsoleLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  await requireSession();
  return <div className="min-h-dvh bg-canvas p-3">{children}</div>;
}
