import type { Metadata } from "next";
import { PageHeader } from "@/shared/ui/page-header";
import { ModulePlaceholder } from "@/shared/ui/module-placeholder";

export const metadata: Metadata = { title: "系统设置" };

export default function SettingsPage() {
  return (
    <>
      <PageHeader
        title="系统设置"
        description="面板级配置：安全、集成、下载代理与自动化默认值。"
      />
      <ModulePlaceholder phase="系统设置 · 建设中">
        分组配置表单与安全策略将在系统管理阶段接入 /api/v1 设置接口后上线。
      </ModulePlaceholder>
    </>
  );
}
