import type { Metadata } from "next";
import { LinkButton } from "@/shared/ui/link-button";
import { PageHeader } from "@/shared/ui/page-header";
import { ModulePlaceholder } from "@/shared/ui/module-placeholder";

export const metadata: Metadata = { title: "服务器详情" };

export default async function ServerDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <>
      <PageHeader
        title={`服务器 #${id}`}
        description="服务器工作区：概览、操作、监控、配置、插件、地图、文件与控制台。"
        actions={
          <LinkButton href="/servers" variant="outline">
            返回列表
          </LinkButton>
        }
      />
      <ModulePlaceholder phase="服务器工作区 · 建设中">
        可分享的子路由工作区将替代旧版横向 Tab，在生命周期与运维阶段逐步接入。
      </ModulePlaceholder>
    </>
  );
}
