"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { Route } from "next";
import { revalidateAfterServerDeleteAction } from "@/modules/servers/actions";
import { randomDeleteCode } from "@/modules/servers/delete-challenge";
import { deleteServerRecord } from "@/modules/servers/delete-server-client";
import { confirm, notify } from "@/shared/feedback";
import { Button } from "@/shared/ui/button";

export function DeleteServerButton({
  serverId,
  name,
  redirectToList = false,
}: {
  serverId: number;
  name: string;
  redirectToList?: boolean;
}) {
  const t = useTranslations("servers");
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function onDelete() {
    const first = await confirm({
      title: t("deleteTitle"),
      description: t("deleteFirst", { name }),
      confirmLabel: t("deleteFirstConfirm"),
      tone: "danger",
    });
    if (!first) return;

    const code = randomDeleteCode();
    const second = await confirm({
      title: t("deleteChallengeTitle"),
      description: t("deleteChallengeHelp", { name, code }),
      confirmLabel: t("deleteConfirm"),
      challenge: code,
      challengeLabel: t("deleteChallengeLabel"),
      tone: "danger",
    });
    if (!second) return;

    setPending(true);
    const result = await deleteServerRecord(serverId);
    if (result.ok) {
      await revalidateAfterServerDeleteAction(serverId).catch(() => undefined);
    }
    setPending(false);
    if (!result.ok) {
      notify.error(result.error || t("deleteFailed"));
      return;
    }
    notify.success(t("deleted", { name }));
    if (redirectToList) {
      router.push("/servers" as Route);
      return;
    }
    router.refresh();
  }

  return (
    <Button
      type="button"
      variant="danger"
      size="sm"
      disabled={pending}
      data-testid={`server-delete-${serverId}`}
      onClick={() => void onDelete()}
    >
      {pending ? t("deleting") : t("delete")}
    </Button>
  );
}
