import { useEffect, useRef } from "react";
import { useTranslations } from "next-intl";
import {
  resetFilesUpload,
  setFilesUploadItems,
  setFilesUploadRate,
} from "@/modules/files/files-upload-store";
import {
  MAX_UPLOAD_FILES,
  toUploadItems,
  uploadFileWithProgress,
  type LocalUpload,
} from "@/modules/files/upload";
import { notify } from "@/shared/feedback";

export function useFilesUpload(options: {
  serverId: number;
  destPath: string;
  setPending: (key: string | null) => void;
  onUploaded: () => Promise<unknown>;
}) {
  const t = useTranslations("files");
  const uploadAbortRef = useRef<AbortController | null>(null);
  const destPathRef = useRef(options.destPath);
  const onUploadedRef = useRef(options.onUploaded);
  const setPendingRef = useRef(options.setPending);
  const serverIdRef = useRef(options.serverId);
  const aliveRef = useRef(true);

  useEffect(() => {
    destPathRef.current = options.destPath;
    onUploadedRef.current = options.onUploaded;
    setPendingRef.current = options.setPending;
    serverIdRef.current = options.serverId;
  }, [options.destPath, options.onUploaded, options.setPending, options.serverId]);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      resetFilesUpload();
    };
  }, []);

  async function startUpload(files: LocalUpload[]) {
    if (files.length === 0) {
      notify.error(t("uploadEmpty"));
      return;
    }
    if (files.length > MAX_UPLOAD_FILES) {
      notify.error(t("uploadTooMany", { max: MAX_UPLOAD_FILES }));
      return;
    }
    const destPath = destPathRef.current;
    const items = toUploadItems(files);
    setFilesUploadItems(items);
    const controller = new AbortController();
    uploadAbortRef.current = controller;
    setPendingRef.current("upload");
    const started = performance.now();
    let completedBytes = 0;
    let done = 0;
    let failed = 0;
    let cancelled = false;
    try {
      for (let index = 0; index < files.length; index += 1) {
        const local = files[index];
        if (!local) continue;
        if (controller.signal.aborted) {
          cancelled = true;
          setFilesUploadItems((current) =>
            current.map((item) =>
              item.status === "queued" || item.status === "uploading"
                ? { ...item, status: "cancelled" }
                : item,
            ),
          );
          break;
        }
        setFilesUploadItems((current) =>
          current.map((item, itemIndex) =>
            itemIndex === index ? { ...item, status: "uploading" } : item,
          ),
        );
        try {
          await uploadFileWithProgress({
            serverId: serverIdRef.current,
            destPath,
            file: local.file,
            relativePath: local.relativePath,
            signal: controller.signal,
            onProgress: (loaded, total) => {
              if (!aliveRef.current) return;
              const elapsed = (performance.now() - started) / 1000;
              setFilesUploadRate(elapsed > 0.15 ? (completedBytes + loaded) / elapsed : 0);
              setFilesUploadItems((current) =>
                current.map((item, itemIndex) =>
                  itemIndex === index
                    ? { ...item, loaded, size: total > 0 ? total : item.size }
                    : item,
                ),
              );
            },
          });
          completedBytes += local.file.size;
          done += 1;
          setFilesUploadItems((current) =>
            current.map((item, itemIndex) =>
              itemIndex === index ? { ...item, status: "done", loaded: item.size } : item,
            ),
          );
        } catch (error) {
          if (error instanceof DOMException && error.name === "AbortError") {
            cancelled = true;
            break;
          }
          failed += 1;
          const message = error instanceof Error ? error.message : t("uploadFailed");
          setFilesUploadItems((current) =>
            current.map((item, itemIndex) =>
              itemIndex === index ? { ...item, status: "error", error: message } : item,
            ),
          );
        }
      }
      if (aliveRef.current) {
        if (cancelled) {
          notify.error(t("uploadCancelled"));
        } else if (failed > 0) {
          notify.error(t("uploadPartial", { done, total: files.length, failed }));
        } else {
          notify.success(t("uploaded"));
          resetFilesUpload();
        }
        if (done > 0) await onUploadedRef.current();
      }
    } finally {
      if (aliveRef.current) {
        setPendingRef.current(null);
        setFilesUploadRate(0);
      }
      uploadAbortRef.current = null;
    }
  }

  function cancelUpload() {
    uploadAbortRef.current?.abort();
  }

  return { startUpload, cancelUpload };
}
