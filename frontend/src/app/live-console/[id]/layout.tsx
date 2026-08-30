import { requireSession } from "@/modules/auth/session";

export default async function LiveConsoleLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  await requireSession();
  return (
    <div className="min-h-0 flex-1 overflow-hidden bg-canvas p-3">{children}</div>
  );
}
