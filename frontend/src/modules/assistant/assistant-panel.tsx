import { getTranslations } from "next-intl/server";
import { TriangleAlert } from "lucide-react";
import { getAssistantConversation, getAssistantWorkspace } from "@/modules/assistant/api";
import { AssistantChat } from "@/modules/assistant/assistant-chat";
import type { AssistantServerOption } from "@/modules/assistant/types";
import { getSession } from "@/modules/auth/session";
import { listServers } from "@/modules/servers/api";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function AssistantPanel({
  conversationId,
  initialDraft,
}: {
  conversationId?: string;
  initialDraft?: string;
}) {
  const t = await getTranslations("assistant");
  const [workspace, session] = await Promise.all([
    getAssistantWorkspace(),
    getSession(),
  ]);
  if (!workspace.ok) {
    return (
      <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>{t("fetchError", { status: workspace.status || "network" })}</span>
      </Card>
    );
  }
  const [detail, servers] = await Promise.all([
    conversationId ? getAssistantConversation(conversationId) : Promise.resolve(null),
    listServers(session?.isAdmin ? "all" : "mine"),
  ]);
  const serverOptions: AssistantServerOption[] = servers.ok
    ? servers.data.map((item) => ({
        id: item.id,
        name: item.name,
        host: item.host,
        gamePort: item.gamePort,
        sshUser: item.sshUser,
        status: item.status,
      }))
    : [];
  return (
    <AssistantChat
      initial={workspace.data}
      initialDetail={detail && detail.ok ? detail.data : null}
      initialDraft={initialDraft}
      servers={serverOptions}
    />
  );
}

export function AssistantPanelSkeleton() {
  return (
    <div className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
      <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
        <Skeleton className="mb-3 h-4 w-32" />
        <Skeleton className="h-8 w-full" />
      </div>
      <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
        <Skeleton className="h-80 w-full" />
      </div>
    </div>
  );
}
