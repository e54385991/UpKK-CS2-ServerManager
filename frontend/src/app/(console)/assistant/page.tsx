import type { Metadata } from "next";
import { PageHeader } from "@/shared/ui/page-header";
import { ModulePlaceholder } from "@/shared/ui/module-placeholder";

export const metadata: Metadata = { title: "AI 助手" };

export default function AssistantPage() {
  return (
    <>
      <PageHeader
        title="AI 助手"
        description="以对话方式排障与运维，工具调用需显式审批后执行。"
      />
      <ModulePlaceholder phase="AI 助手 · 建设中">
        流式对话、工具审批与可重放事件将在 AI 阶段基于可重放 SSE 接入。
      </ModulePlaceholder>
    </>
  );
}
