"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { getFileContentAction } from "@/modules/files/actions";
import type { FilesBanner } from "@/modules/files/files-banner";
import { RequestGate } from "@/modules/files/files-request-gate";
import type { EditorFile } from "@/modules/files/lazy-dialogs";
import type { FileEntry } from "@/modules/files/types";

export function useFilesEditor(options: {
  serverId: number;
  setBanner: (banner: FilesBanner | null) => void;
}) {
  const t = useTranslations("files");
  const [editing, setEditing] = useState<EditorFile | null>(null);
  const gateRef = useRef(new RequestGate());
  const setBannerRef = useRef(options.setBanner);
  const serverIdRef = useRef(options.serverId);

  useEffect(() => {
    setBannerRef.current = options.setBanner;
    serverIdRef.current = options.serverId;
  }, [options.setBanner, options.serverId]);

  useEffect(() => {
    const gate = gateRef.current;
    return () => {
      gate.invalidate();
    };
  }, []);

  async function openEditor(entry: FileEntry) {
    const requestId = gateRef.current.next();
    setEditing({ path: entry.path, name: entry.name, content: "", loading: true });
    const result = await getFileContentAction(serverIdRef.current, entry.path);
    if (!gateRef.current.isCurrent(requestId)) return;
    if (!result.ok) {
      setEditing(null);
      setBannerRef.current({ tone: "danger", text: result.error || t("failed") });
      return;
    }
    setEditing({
      path: result.data.path,
      name: entry.name,
      content: result.data.content,
    });
  }

  function closeEditor() {
    gateRef.current.invalidate();
    setEditing(null);
  }

  return { editing, openEditor, closeEditor };
}
