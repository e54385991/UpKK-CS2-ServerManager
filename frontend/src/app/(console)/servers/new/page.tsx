import type { Metadata } from "next";
import { PageHeader } from "@/shared/ui/page-header";
import { LinkButton } from "@/shared/ui/link-button";
import { ModulePlaceholder } from "@/shared/ui/module-placeholder";

export const metadata: Metadata = { title: "添加服务器" };

export default function NewServerPage() {
  return (
    <>
      <PageHeader
        title="添加服务器"
        description="通过 SSH 连接一台主机，随后即可一键部署 Counter-Strike 2。"
        actions={
          <LinkButton href="/servers" variant="outline">
            返回列表
          </LinkButton>
        }
      />
      <ModulePlaceholder phase="初始化向导 · 建设中">
        SSH 校验、CAPTCHA 与部署向导将在服务器生命周期阶段接入 /api/v1 后上线。
      </ModulePlaceholder>
    </>
  );
}
