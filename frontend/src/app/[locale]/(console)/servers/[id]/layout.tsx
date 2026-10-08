import { WorkspaceFrame } from "@/modules/servers/workspace-frame";

export default function ServerWorkspaceLayout({ children, params }: {
  children: React.ReactNode;
  params: Promise<{ id: string }>;
}) {
  return <WorkspaceFrame params={params}>{children}</WorkspaceFrame>;
}
