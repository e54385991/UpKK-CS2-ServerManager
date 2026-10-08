/**
 * Public pages sit in the root `h-dvh overflow-hidden` frame. This region is
 * the scroll container so long pages (auth forms on small viewports) can move.
 */
export default function PublicLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>;
}
