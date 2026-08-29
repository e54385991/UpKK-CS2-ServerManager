import type { Metadata } from "next";
import { getSession } from "@/modules/auth/session";
import { PageHeader } from "@/shared/ui/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/ui/card";
import { Badge } from "@/shared/ui/badge";

export const metadata: Metadata = { title: "个人资料" };

export default async function ProfilePage() {
  const session = await getSession();

  return (
    <>
      <PageHeader title="个人资料" description="查看你的账号信息与角色。" />
      <Card className="max-w-xl">
        <CardHeader>
          <CardTitle>账号</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field label="用户名" value={session?.username ?? "—"} />
          <Field label="邮箱" value={session?.email ?? "—"} />
          <div className="flex items-center justify-between">
            <span className="text-sm text-fg-muted">角色</span>
            <Badge tone={session?.isAdmin ? "primary" : "neutral"}>
              {session?.isAdmin ? "管理员" : "普通用户"}
            </Badge>
          </div>
        </CardContent>
      </Card>
    </>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-fg-muted">{label}</span>
      <span className="text-sm font-medium text-fg">{value}</span>
    </div>
  );
}
