"use client";

import { useTranslations } from "next-intl";
import { LoaderCircle } from "lucide-react";
import { Dialog } from "@/shared/ui/dialog";

export function DialogContentLoading() {
  const t = useTranslations("feedback");
  return <p role="status" className="flex min-h-24 items-center justify-center gap-2 text-sm text-fg-muted">
    <LoaderCircle className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
    {t("loading")}
  </p>;
}

export function DialogLoading({ title, open = true, onClose }: {
  title: string; open?: boolean; onClose: () => void;
}) {
  const t = useTranslations("feedback");
  return <Dialog open={open} title={title} closeLabel={t("cancel")} onClose={onClose}>
    <DialogContentLoading />
  </Dialog>;
}
