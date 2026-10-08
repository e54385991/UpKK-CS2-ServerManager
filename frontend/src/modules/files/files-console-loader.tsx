"use client";
import dynamic from "next/dynamic";
import { FilesPanelSkeleton } from "./files-panel-skeleton";

// File controls require handlers; keep the same skeleton while their code loads.
export const FilesConsole = dynamic(() => import("./files-console").then(module => module.FilesConsole), {
  ssr: false,
  loading: FilesPanelSkeleton,
});
