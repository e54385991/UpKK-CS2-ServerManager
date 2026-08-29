import { Suspense } from "react";
import type { Metadata } from "next";
import { Plus } from "lucide-react";
import { PageHeader } from "@/shared/ui/page-header";
import { LinkButton } from "@/shared/ui/link-button";
import { ServerList, ServerListSkeleton } from "@/modules/servers/server-list";

export const metadata: Metadata = { title: "服务器" };

export default function ServersPage() {
  return (
    <>
      <PageHeader
        title="服务器"
        description="集中查看、部署与运维你的 Counter-Strike 2 服务器。"
        actions={
          <LinkButton href="/servers/new">
            <Plus className="size-4" />
            添加服务器
          </LinkButton>
        }
      />
      <Suspense fallback={<ServerListSkeleton />}>
        <ServerList />
      </Suspense>
    </>
  );
}
