import type { Metadata } from "next";
import { PageHeader } from "@/shared/ui/page-header";
import { ModulePlaceholder } from "@/shared/ui/module-placeholder";

export const metadata: Metadata = { title: "审计日志" };

export default function AuditPage() {
  return (
    <>
      <PageHeader
        title="审计日志"
        description="记录关键操作与安全事件，支持按角色、动作与时间检索。"
      />
      <ModulePlaceholder phase="审计日志 · 建设中">
        分页、筛选与导出将在管理阶段接入 /api/v1 审计接口后上线。
      </ModulePlaceholder>
    </>
  );
}
