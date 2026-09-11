"use client";

import { Suspense, type ComponentProps } from "react";
import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";
import { DialogLoading } from "@/shared/ui/dialog-loading";
import type { ExtractDialog as Extract } from "@/modules/files/extract-dialog";
import type { RenameDialog as Rename } from "@/modules/files/rename-dialog";
import type { FileEditorDialog as Editor } from "@/modules/files/file-editor-dialog";
export type { EditorFile } from "@/modules/files/file-editor-dialog";

const LazyExtract = dynamic(() => import("@/modules/files/extract-dialog").then(mod => mod.ExtractDialog));
const LazyRename = dynamic(() => import("@/modules/files/rename-dialog").then(mod => mod.RenameDialog));
const LazyEditor = dynamic(() => import("@/modules/files/file-editor-dialog").then(mod => mod.FileEditorDialog));

export function ExtractDialog(props: ComponentProps<typeof Extract>) {
  const t = useTranslations("files");
  return <Suspense fallback={<DialogLoading title={t("extractTitle")} onClose={props.onClose} />}>
    <LazyExtract {...props} />
  </Suspense>;
}

export function RenameDialog(props: ComponentProps<typeof Rename>) {
  const t = useTranslations("files");
  return <Suspense fallback={<DialogLoading title={t("rename")} onClose={props.onClose} />}>
    <LazyRename {...props} />
  </Suspense>;
}

export function FileEditorDialog(props: ComponentProps<typeof Editor>) {
  return <Suspense fallback={<DialogLoading title={props.file.name} onClose={props.onClose} />}>
    <LazyEditor {...props} />
  </Suspense>;
}
