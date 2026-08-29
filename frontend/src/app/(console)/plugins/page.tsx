import type { Metadata } from "next";
import { PageHeader } from "@/shared/ui/page-header";
import { ModulePlaceholder } from "@/shared/ui/module-placeholder";

export const metadata: Metadata = { title: "插件中心" };

export default function PluginsPage() {
  return (
    <>
      <PageHeader
        title="插件中心"
        description="浏览、安装与更新 Metamod、CounterStrikeSharp 及插件市场内容。"
      />
      <ModulePlaceholder phase="插件中心 · 建设中">
        插件市场、依赖与冲突审批、批量安装将在插件阶段接入 /api/v1 后上线。
      </ModulePlaceholder>
    </>
  );
}
