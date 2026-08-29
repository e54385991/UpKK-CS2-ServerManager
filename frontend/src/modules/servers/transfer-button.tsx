"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { ArrowDownUp } from "lucide-react";
import { ServerTransferDialog } from "@/modules/servers/transfer-dialog";
import type { TransferServerOption } from "@/modules/servers/types";
import { Button } from "@/shared/ui/button";

export function ServerTransferButton({
  servers,
}: {
  servers: readonly TransferServerOption[];
}) {
  const t = useTranslations("servers");
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button type="button" variant="outline" onClick={() => setOpen(true)}>
        <ArrowDownUp />
        {t("transfer.open")}
      </Button>
      <ServerTransferDialog
        open={open}
        servers={servers}
        onClose={() => setOpen(false)}
      />
    </>
  );
}
