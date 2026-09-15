import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { listFilesAction } from "@/modules/files/actions";
import type { FilesBanner } from "@/modules/files/files-banner";
import { RequestGate } from "@/modules/files/files-request-gate";
import {
  filesHref,
  isMissingPathError,
  replaceFilesUrl,
} from "@/modules/files/paths";
import {
  filterAndSortEntries,
  type FileKindFilter,
  type FileSortDir,
  type FileSortKey,
  type FilesWorkspace,
} from "@/modules/files/types";

export function useFilesDirectory(
  initial: FilesWorkspace,
  options: {
    setPending: (key: string | null) => void;
    setBanner: (banner: FilesBanner | null) => void;
    onNavigate: () => void;
  },
) {
  const t = useTranslations("files");
  const listAnchorRef = useRef<HTMLDivElement>(null);
  const gateRef = useRef(new RequestGate());
  const pathRef = useRef(initial.path);
  const optionsRef = useRef(options);
  const [workspace, setWorkspace] = useState(initial);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<FileKindFilter>("all");
  const [sortKey, setSortKey] = useState<FileSortKey>("name");
  const [sortDir, setSortDir] = useState<FileSortDir>("asc");

  useEffect(() => {
    optionsRef.current = options;
  });

  useEffect(() => {
    pathRef.current = workspace.path;
  }, [workspace.path]);

  useEffect(() => {
    const gate = gateRef.current;
    return () => {
      gate.invalidate();
    };
  }, []);

  const listedFiles = useMemo(
    () => filterAndSortEntries(workspace.files, query, kind, sortKey, sortDir),
    [kind, query, sortDir, sortKey, workspace.files],
  );
  const totalFiles = useMemo(
    () => workspace.files.filter((entry) => entry.name !== "." && entry.name !== "..").length,
    [workspace.files],
  );
  const filtering = Boolean(query.trim()) || kind !== "all";

  const load = useCallback(
    async (path: string): Promise<FilesWorkspace | null> => {
      const requestId = gateRef.current.next();
      const changingDir = path !== pathRef.current;
      if (changingDir) {
        optionsRef.current.setPending("browse");
        optionsRef.current.onNavigate();
      }
      const result = await listFilesAction(workspace.serverId, path);
      if (!gateRef.current.isCurrent(requestId)) return null;
      if (!result.ok) {
        if (changingDir) optionsRef.current.setPending(null);
        optionsRef.current.setBanner({ tone: "danger", text: result.error || t("failed") });
        return null;
      }
      if (
        (!result.data.sshOk && isMissingPathError(result.data.sshError)) ||
        (result.data.sshOk && result.data.message && result.data.files.length === 0)
      ) {
        if (changingDir) optionsRef.current.setPending(null);
        optionsRef.current.setBanner({
          tone: "danger",
          text: t("pathMissing"),
        });
        return null;
      }
      setWorkspace(result.data);
      setQuery((current) => (result.data.path === pathRef.current ? current : ""));
      replaceFilesUrl(filesHref(result.data.serverId, result.data.root, result.data.path));
      if (changingDir) {
        window.requestAnimationFrame(() => {
          if (!gateRef.current.isCurrent(requestId)) return;
          listAnchorRef.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
          window.requestAnimationFrame(() => {
            if (!gateRef.current.isCurrent(requestId)) return;
            optionsRef.current.setPending(null);
          });
        });
      }
      return result.data;
    },
    [t, workspace.serverId],
  );

  function toggleSort(next: FileSortKey) {
    if (sortKey === next) {
      setSortDir((current) => (current === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(next);
    setSortDir(next === "name" ? "asc" : "desc");
  }

  return {
    workspace,
    query,
    setQuery,
    kind,
    setKind,
    sortKey,
    sortDir,
    listedFiles,
    totalFiles,
    filtering,
    listAnchorRef,
    load,
    toggleSort,
  };
}
