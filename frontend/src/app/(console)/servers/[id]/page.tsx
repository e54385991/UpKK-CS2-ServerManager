import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { LinkButton } from "@/shared/ui/link-button";
import { PageHeader } from "@/shared/ui/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/ui/card";
import { Badge, StatusDot } from "@/shared/ui/badge";
import { ModulePlaceholder } from "@/shared/ui/module-placeholder";
import { getServer } from "@/modules/servers/api";
import { SERVER_STATUS_META } from "@/modules/servers/types";

export const metadata: Metadata = { title: "服务器详情" };

export default async function ServerDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = Number(id);
  if (!Number.isInteger(serverId)) notFound();

  const result = await getServer(serverId);
  if (!result.ok && result.status === 404) notFound();

  const server = result.ok ? result.data : null;
  const meta = server ? SERVER_STATUS_META[server.status] : null;

  return (
    <>
      <PageHeader
        title={
          <span className="flex items-center gap-3">
            {server ? server.name : `服务器 #${id}`}
            {meta ? (
              <Badge tone={meta.tone}>
                <StatusDot tone={meta.tone} pulse={server?.status === "running"} />
                {meta.label}
              </Badge>
            ) : null}
          </span>
        }
        description="服务器工作区：概览、操作、监控、配置、插件、地图、文件与控制台。"
        actions={
          <LinkButton href="/servers" variant="outline">
            返回列表
          </LinkButton>
        }
      />

      {server ? (
        <Card className="mb-6 max-w-2xl">
          <CardHeader>
            <CardTitle>连接与运行</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
            <Field label="地址" value={`${server.host}:${server.gamePort}`} mono />
            <Field label="SSH" value={`${server.sshUser}@${server.host}:${server.sshPort}`} mono />
            <Field label="默认地图" value={server.defaultMap} />
            <Field label="最大人数" value={`${server.maxPlayers}`} />
            <Field label="游戏模式" value={`${server.gameMode} / ${server.gameType}`} />
            <Field label="目录" value={server.gameDirectory} mono />
          </CardContent>
        </Card>
      ) : (
        <Card className="mb-6 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
          暂时无法获取该服务器详情（{result.ok ? "" : result.status || "网络错误"}）。
        </Card>
      )}

      <ModulePlaceholder phase="服务器工作区 · 建设中">
        可分享的子路由工作区将替代旧版横向 Tab，在生命周期与运维阶段逐步接入。
      </ModulePlaceholder>
    </>
  );
}

function Field({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="space-y-0.5">
      <p className="text-xs text-fg-subtle">{label}</p>
      <p className={mono ? "font-mono text-sm text-fg" : "text-sm text-fg"}>
        {value}
      </p>
    </div>
  );
}
