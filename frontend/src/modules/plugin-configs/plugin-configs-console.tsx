"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import {
  ArrowUp,
  FileCode,
  Folder,
  FolderOpen,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import {
  browsePluginConfigPathAction,
  createPluginConfigSourceAction,
  deletePluginConfigSourceAction,
  getPluginConfigFileAction,
  listPluginConfigSourcesAction,
  restoreDefaultPluginConfigSourcesAction,
  savePluginConfigFileAction,
} from "@/modules/plugin-configs/actions";
import {
  formatConfigSize,
  formatConfigTimestamp,
  groupConfigFields,
  groupConfigFiles,
  type PluginConfigBrowseItem,
  type PluginConfigEditMode,
  type PluginConfigField,
  type PluginConfigFieldValue,
  type PluginConfigFile,
  type PluginConfigScannedFile,
  type PluginConfigSource,
  type PluginConfigWorkspace,
} from "@/modules/plugin-configs/types";
import { confirm } from "@/shared/feedback";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Input, Label } from "@/shared/ui/input";
import { Switch } from "@/shared/ui/switch";
import { Textarea } from "@/shared/ui/textarea";
import { cn } from "@/shared/lib/cn";

type Banner = { readonly tone: "ok" | "warn" | "danger"; readonly text: string };

type SourceRuntime = {
  readonly loaded: boolean;
  readonly loading: boolean;
  readonly files: readonly PluginConfigScannedFile[];
  readonly fileCount: number;
  readonly truncated: boolean;
  readonly scanPath: string;
};

type ScanEvent =
  | { type: "start" }
  | { type: "progress"; directory?: string; count?: number }
  | { type: "file"; file?: Record<string, unknown> }
  | { type: "complete"; count?: number; truncated?: boolean }
  | { type: "error"; detail?: string };

const EMPTY_RUNTIME: SourceRuntime = {
  loaded: false,
  loading: false,
  files: [],
  fileCount: 0,
  truncated: false,
  scanPath: "",
};

function toScannedFile(raw: Record<string, unknown>): PluginConfigScannedFile {
  return {
    name: String(raw.name || ""),
    path: String(raw.path || ""),
    treePath: String(raw.tree_path || raw.treePath || ""),
    size: Number(raw.size || 0),
    modified: Number(raw.modified || 0),
    format: String(raw.format || "raw"),
    tooLarge: Boolean(raw.too_large ?? raw.tooLarge),
  };
}

function valuesFromFile(file: PluginConfigFile): Record<string, PluginConfigFieldValue> {
  const values: Record<string, PluginConfigFieldValue> = {};
  for (const field of file.fields) values[field.id] = field.value;
  return values;
}

function parseFieldInput(field: PluginConfigField, value: string | boolean): PluginConfigFieldValue {
  if (field.kind === "boolean") return Boolean(value);
  if (field.kind === "integer") {
    if (value === "") return null;
    const parsed = Number.parseInt(String(value), 10);
    return Number.isNaN(parsed) ? null : parsed;
  }
  if (field.kind === "number") {
    if (value === "") return null;
    const parsed = Number(value);
    return Number.isNaN(parsed) ? null : parsed;
  }
  return String(value);
}

export function PluginConfigsConsole({
  initial,
}: {
  initial: PluginConfigWorkspace;
}) {
  const t = useTranslations("pluginConfigs");
  const [workspace, setWorkspace] = useState(initial);
  const [runtime, setRuntime] = useState<Record<number, SourceRuntime>>({});
  const [activeSourceId, setActiveSourceId] = useState<number | null>(
    initial.sources[0]?.id ?? null,
  );
  const [showAddSource, setShowAddSource] = useState(false);
  const [sourcePath, setSourcePath] = useState("");
  const [showBrowser, setShowBrowser] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const [browsePath, setBrowsePath] = useState(".");
  const [browseItems, setBrowseItems] = useState<readonly PluginConfigBrowseItem[]>([]);
  const [fileSearch, setFileSearch] = useState("");
  const [fieldSearch, setFieldSearch] = useState("");
  const [selectedFile, setSelectedFile] = useState<PluginConfigScannedFile | null>(null);
  const [fileData, setFileData] = useState<PluginConfigFile | null>(null);
  const [editMode, setEditMode] = useState<PluginConfigEditMode>("visual");
  const [fieldValues, setFieldValues] = useState<Record<string, PluginConfigFieldValue>>({});
  const [originalFieldValues, setOriginalFieldValues] = useState<
    Record<string, PluginConfigFieldValue>
  >({});
  const [rawContent, setRawContent] = useState("");
  const [originalRawContent, setOriginalRawContent] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<Banner | null>(null);

  const serverId = workspace.serverId;
  const activeSource =
    workspace.sources.find((source) => source.id === activeSourceId) ?? null;
  const activeRuntime =
    activeSource?.id != null ? (runtime[activeSource.id] ?? EMPTY_RUNTIME) : EMPTY_RUNTIME;
  const dirty = Boolean(
    fileData &&
      (editMode === "raw"
        ? rawContent !== originalRawContent
        : JSON.stringify(fieldValues) !== JSON.stringify(originalFieldValues)),
  );

  const fileGroups = useMemo(
    () => groupConfigFiles(activeRuntime.files, fileSearch, t("rootFolder")),
    [activeRuntime.files, fileSearch, t],
  );
  const fieldGroups = useMemo(
    () => (fileData ? groupConfigFields(fileData.fields, fieldSearch) : []),
    [fileData, fieldSearch],
  );

  useEffect(() => {
    function onBeforeUnload(event: BeforeUnloadEvent) {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  async function confirmDiscard(): Promise<boolean> {
    if (!dirty) return true;
    return confirm({
      description: t("discardConfirm"),
      tone: "default",
    });
  }

  function clearEditor() {
    setSelectedFile(null);
    setFileData(null);
    setFieldValues({});
    setOriginalFieldValues({});
    setRawContent("");
    setOriginalRawContent("");
  }

  function applyFileData(data: PluginConfigFile) {
    setFileData(data);
    setRawContent(data.content);
    setOriginalRawContent(data.content);
    const values = valuesFromFile(data);
    setFieldValues(values);
    setOriginalFieldValues(structuredClone(values));
    setEditMode(data.visualSupported ? "visual" : "raw");
    if (data.message) setBanner({ tone: "ok", text: data.message });
  }

  function patchRuntime(sourceId: number, patch: Partial<SourceRuntime>) {
    setRuntime((current) => {
      const previous = current[sourceId] ?? EMPTY_RUNTIME;
      return { ...current, [sourceId]: { ...previous, ...patch } };
    });
  }

  async function reloadSources(preferredId: number | null = activeSourceId) {
    const result = await listPluginConfigSourcesAction(serverId);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error || t("loadSourcesFailed") });
      return;
    }
    setWorkspace(result.data);
    const preferred = result.data.sources.find((source) => source.id === preferredId);
    const current = result.data.sources.find((source) => source.id === activeSourceId);
    const nextId = preferred?.id ?? current?.id ?? result.data.sources[0]?.id ?? null;
    if (nextId !== activeSourceId) {
      setActiveSourceId(nextId);
      clearEditor();
    }
  }

  async function refreshSources() {
    if (pending || !(await confirmDiscard())) return;
    setPending("refresh");
    setBanner(null);
    try {
      await reloadSources(activeSourceId);
      setBanner({ tone: "ok", text: t("sourcesReloaded") });
    } finally {
      setPending(null);
    }
  }

  async function selectSource(source: PluginConfigSource) {
    if (source.id == null || source.id === activeSourceId) return;
    if (!(await confirmDiscard())) return;
    setActiveSourceId(source.id);
    clearEditor();
  }

  async function loadSource(source: PluginConfigSource) {
    if (source.id == null || !(await confirmDiscard())) return;
    const sourceId = source.id;
    setActiveSourceId(sourceId);
    clearEditor();
    patchRuntime(sourceId, {
      loading: true,
      loaded: true,
      files: [],
      fileCount: 0,
      truncated: false,
      scanPath: ".",
    });
    setBanner(null);
    const files: PluginConfigScannedFile[] = [];
    try {
      const response = await fetch(
        `/plugin-config-scan/servers/${serverId}/sources/${sourceId}`,
        { method: "POST" },
      );
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || t("scanFailed"));
      }
      if (!response.body) throw new Error(t("streamUnavailable"));

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let completed = false;

      const handleEvent = (event: ScanEvent) => {
        if (event.type === "progress") {
          patchRuntime(sourceId, {
            scanPath: event.directory ?? ".",
            fileCount: event.count ?? files.length,
          });
        } else if (event.type === "file" && event.file) {
          files.push(toScannedFile(event.file));
          patchRuntime(sourceId, { files: [...files], fileCount: files.length });
        } else if (event.type === "complete") {
          files.sort((left, right) => left.treePath.localeCompare(right.treePath));
          patchRuntime(sourceId, {
            files,
            fileCount: event.count ?? files.length,
            truncated: Boolean(event.truncated),
            scanPath: "",
          });
          completed = true;
        } else if (event.type === "error") {
          throw new Error(event.detail || t("scanFailed"));
        }
      };

      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        let newline = buffer.indexOf("\n");
        while (newline >= 0) {
          const line = buffer.slice(0, newline).trim();
          buffer = buffer.slice(newline + 1);
          if (line) handleEvent(JSON.parse(line) as ScanEvent);
          newline = buffer.indexOf("\n");
        }
        if (done) break;
      }
      if (buffer.trim()) handleEvent(JSON.parse(buffer) as ScanEvent);
      if (!completed) throw new Error(t("streamInterrupted"));
    } catch (error) {
      patchRuntime(sourceId, {
        loaded: files.length > 0,
        files,
        fileCount: files.length,
        scanPath: "",
      });
      setBanner({
        tone: "danger",
        text: `${t("scanFailed")}: ${error instanceof Error ? error.message : t("failed")}`,
      });
    } finally {
      patchRuntime(sourceId, { loading: false, scanPath: "" });
    }
  }

  async function addSource() {
    const path = sourcePath.trim();
    if (!path || pending) return;
    setPending("add");
    setBanner(null);
    try {
      const created = await createPluginConfigSourceAction(serverId, path);
      if (!created.ok) {
        setBanner({ tone: "danger", text: created.error || t("addSourceFailed") });
        return;
      }
      await reloadSources(created.data.id);
      if (created.data.id == null) {
        setBanner({ tone: "danger", text: t("persistenceFailed") });
        return;
      }
      setSourcePath("");
      setShowAddSource(false);
      setShowBrowser(false);
      setBanner({ tone: "ok", text: t("sourceAdded") });
    } finally {
      setPending(null);
    }
  }

  async function removeSource(source: PluginConfigSource) {
    if (source.id == null) return;
    if (source.id === activeSourceId && !(await confirmDiscard())) return;
    if (!(await confirm(t("removeConfirm")))) return;
    setPending(`remove-${source.id}`);
    setBanner(null);
    try {
      const result = await deletePluginConfigSourceAction(serverId, source.id);
      if (!result.ok) {
        setBanner({ tone: "danger", text: result.error || t("removeSourceFailed") });
        return;
      }
      setRuntime((current) => {
        const next = { ...current };
        delete next[source.id!];
        return next;
      });
      await reloadSources();
      setBanner({ tone: "ok", text: t("sourceRemoved") });
    } finally {
      setPending(null);
    }
  }

  async function restoreDefault() {
    if (pending) return;
    setPending("restore");
    setBanner(null);
    try {
      const result = await restoreDefaultPluginConfigSourcesAction(serverId);
      if (!result.ok) {
        setBanner({ tone: "danger", text: result.error || t("restoreFailed") });
        return;
      }
      await reloadSources(result.data.sources[0]?.id ?? null);
      setBanner({ tone: "ok", text: t("defaultRestored") });
    } finally {
      setPending(null);
    }
  }

  async function browse(path: string) {
    setBrowsing(true);
    try {
      const result = await browsePluginConfigPathAction(serverId, path);
      if (!result.ok) {
        setBanner({ tone: "danger", text: result.error || t("browseFailed") });
        return;
      }
      setBrowsePath(result.data.path);
      setBrowseItems(result.data.items);
    } finally {
      setBrowsing(false);
    }
  }

  async function openBrowser() {
    setShowBrowser(true);
    await browse(".");
  }

  function browseUp() {
    if (browsePath === ".") return;
    const parts = browsePath.split("/").filter(Boolean);
    parts.pop();
    void browse(parts.join("/") || ".");
  }

  function chooseBrowsePath(path: string) {
    setSourcePath(path);
    setShowBrowser(false);
  }

  async function loadFile(file: PluginConfigScannedFile, force = false) {
    if (file.tooLarge || activeSource?.id == null) return;
    if (!force && !(await confirmDiscard())) return;
    setSelectedFile(file);
    setPending("load-file");
    setFileData(null);
    setBanner(null);
    try {
      const result = await getPluginConfigFileAction(serverId, activeSource.id, file.path);
      if (!result.ok) {
        setSelectedFile(null);
        setBanner({ tone: "danger", text: result.error || t("loadFileFailed") });
        return;
      }
      applyFileData(result.data);
    } finally {
      setPending(null);
    }
  }

  async function switchMode(mode: PluginConfigEditMode) {
    if (mode === editMode || (mode === "visual" && !fileData?.visualSupported)) return;
    if (
      dirty &&
      !(await confirm({
        description: t("modeDiscardConfirm"),
        tone: "default",
      }))
    ) {
      return;
    }
    if (mode === "raw") setRawContent(originalRawContent);
    else setFieldValues(structuredClone(originalFieldValues));
    setEditMode(mode);
  }

  async function reloadFile() {
    if (!selectedFile || !(await confirmDiscard())) return;
    await loadFile(selectedFile, true);
  }

  async function saveFile() {
    if (!fileData || !dirty || activeSource?.id == null) return;
    setPending("save");
    setBanner(null);
    try {
      const result = await savePluginConfigFileAction(serverId, activeSource.id, {
        path: fileData.path,
        expectedRevision: fileData.revision,
        mode: editMode,
        content: editMode === "raw" ? rawContent : null,
        changes:
          editMode === "visual"
            ? fileData.fields
                .filter(
                  (field) =>
                    JSON.stringify(fieldValues[field.id]) !==
                    JSON.stringify(originalFieldValues[field.id]),
                )
                .map((field) => ({ id: field.id, value: fieldValues[field.id] ?? null }))
            : [],
      });
      if (!result.ok) {
        const prefix = result.status === 409 ? t("conflict") : t("saveFailed");
        setBanner({ tone: "danger", text: `${prefix}: ${result.error}` });
        return;
      }
      applyFileData(result.data);
      setBanner({ tone: "ok", text: result.data.message || t("saved") });
    } finally {
      setPending(null);
    }
  }

  const busy = pending != null;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2">
              <FileCode className="size-4 text-primary" />
              {t("title")}
            </CardTitle>
            <CardDescription>{t("manualLoadHint")}</CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => void refreshSources()}
            >
              <RefreshCw className="size-3.5" />
              {t("reloadSources")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => void restoreDefault()}
            >
              <RotateCcw className="size-3.5" />
              {t("restoreDefault")}
            </Button>
            <Button
              size="sm"
              disabled={busy}
              onClick={() => setShowAddSource((open) => !open)}
            >
              <Plus className="size-3.5" />
              {t("addSource")}
            </Button>
          </div>
        </CardHeader>
        {showAddSource ? (
          <CardContent className="space-y-3 border-t border-line">
            <div>
              <Label htmlFor="plugin-config-source-path">{t("sourcePath")}</Label>
              <div className="flex flex-wrap gap-2">
                <Input
                  id="plugin-config-source-path"
                  className="font-mono"
                  value={sourcePath}
                  placeholder={`${workspace.gameDirectory}/cs2/game/csgo/cfg`}
                  onChange={(event) => setSourcePath(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      void addSource();
                    }
                  }}
                />
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy || browsing}
                  onClick={() => void openBrowser()}
                >
                  <FolderOpen className="size-3.5" />
                  {t("browse")}
                </Button>
                <Button
                  type="button"
                  disabled={busy || !sourcePath.trim()}
                  onClick={() => void addSource()}
                >
                  {t("add")}
                </Button>
              </div>
              <p className="mt-1 text-xs text-fg-subtle">{t("pathHint")}</p>
            </div>
            {showBrowser ? (
              <div className="rounded-md border border-line bg-surface-raised p-3">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={browsePath === "." || browsing}
                    onClick={browseUp}
                  >
                    <ArrowUp className="size-3.5" />
                  </Button>
                  <code className="min-w-0 flex-1 break-all text-xs text-fg-muted">
                    {browsePath}
                  </code>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => chooseBrowsePath(browsePath)}
                  >
                    {t("chooseCurrentFolder")}
                  </Button>
                </div>
                {browsing ? (
                  <p className="py-4 text-center text-sm text-fg-subtle">{t("browsing")}</p>
                ) : (
                  <ul className="max-h-64 space-y-1 overflow-auto">
                    {browseItems.map((item) => (
                      <li key={`${item.type}:${item.path ?? item.name}`}>
                        <button
                          type="button"
                          disabled={!item.selectable}
                          className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-surface disabled:opacity-50"
                          onClick={() => {
                            if (!item.selectable || !item.path) return;
                            if (item.type === "directory") void browse(item.path);
                            else chooseBrowsePath(item.path);
                          }}
                        >
                          {item.type === "directory" ? (
                            <Folder className="size-3.5 text-warn" />
                          ) : (
                            <FileCode className="size-3.5 text-fg-subtle" />
                          )}
                          <span className="flex-1 truncate">{item.name}</span>
                          {item.selectable ? (
                            <span className="text-xs text-primary">{t("select")}</span>
                          ) : null}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ) : null}
          </CardContent>
        ) : null}
      </Card>

      {banner ? (
        <Card
          className={cn(
            "flex items-center gap-3 px-5 py-3 text-sm",
            banner.tone === "ok" && "border-ok/30 bg-ok-muted/40 text-ok",
            banner.tone === "warn" && "border-warn/30 bg-warn-muted/40 text-warn",
            banner.tone === "danger" && "border-danger/30 bg-danger-muted/40 text-danger",
          )}
        >
          <TriangleAlert className="size-4 shrink-0" />
          <span>{banner.text}</span>
        </Card>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="min-h-80">
          <CardHeader className="py-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              {t("sources")}
              <Badge tone="neutral">{workspace.sources.length}</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {workspace.sources.length === 0 ? (
              <p className="py-8 text-center text-sm text-fg-subtle">{t("noSources")}</p>
            ) : (
              workspace.sources.map((source) => {
                const state = source.id != null ? (runtime[source.id] ?? EMPTY_RUNTIME) : EMPTY_RUNTIME;
                const active = source.id === activeSourceId;
                return (
                  <article
                    key={source.id ?? source.path}
                    className={cn(
                      "rounded-md border px-3 py-2",
                      active ? "border-primary/50 bg-primary-muted/40" : "border-line",
                    )}
                  >
                    <button
                      type="button"
                      className="w-full text-left"
                      onClick={() => void selectSource(source)}
                    >
                      <div className="flex items-start gap-2">
                        {source.type === "directory" ? (
                          <Folder className="mt-0.5 size-3.5 text-warn" />
                        ) : (
                          <FileCode className="mt-0.5 size-3.5 text-primary" />
                        )}
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium text-fg">{source.name}</p>
                          <p className="break-all font-mono text-[11px] text-fg-subtle">
                            {source.path}
                          </p>
                          <div className="mt-1 flex flex-wrap gap-1">
                            {source.isDefault ? <Badge tone="info">{t("defaultSource")}</Badge> : null}
                            {source.persisted ? <Badge tone="ok">{t("persisted")}</Badge> : null}
                          </div>
                        </div>
                      </div>
                    </button>
                    <div className="mt-2 flex gap-1">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="flex-1"
                        disabled={busy || state.loading || source.id == null}
                        onClick={() => void loadSource(source)}
                      >
                        <RefreshCw className="size-3.5" />
                        {t("loadConfiguration")}
                      </Button>
                      <Button
                        type="button"
                        variant="danger"
                        size="icon"
                        disabled={busy || source.id == null}
                        aria-label={t("delete")}
                        onClick={() => void removeSource(source)}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                    {state.loading ? (
                      <p className="mt-1 text-xs text-info">
                        {t("scanning")}: {state.fileCount} {t("filesFound")}
                        {state.scanPath ? (
                          <>
                            {" · "}
                            <code>{state.scanPath}</code>
                          </>
                        ) : null}
                      </p>
                    ) : null}
                    {state.loaded && !state.loading ? (
                      <p
                        className={cn(
                          "mt-1 text-xs",
                          state.truncated ? "text-warn" : "text-ok",
                        )}
                      >
                        {state.fileCount} {t("filesFound")}
                        {state.truncated ? ` ${t("truncated")}` : ""}
                      </p>
                    ) : null}
                  </article>
                );
              })
            )}
          </CardContent>
        </Card>

        <Card className="min-h-80">
          <CardHeader className="py-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              {t("files")}
              <Badge tone="neutral">{activeRuntime.files.length}</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <Input
              value={fileSearch}
              onChange={(event) => setFileSearch(event.target.value)}
              placeholder={t("searchFiles")}
            />
            {!activeSource || !activeRuntime.loaded ? (
              <p className="py-8 text-center text-sm text-fg-subtle">{t("clickLoad")}</p>
            ) : fileGroups.length === 0 ? (
              <p className="py-8 text-center text-sm text-fg-subtle">{t("noFiles")}</p>
            ) : (
              <div className="max-h-[32rem] space-y-2 overflow-auto">
                {fileGroups.map((group) => (
                  <div key={group.path || "$root"}>
                    <p
                      className="text-xs font-medium text-fg-subtle"
                      style={{ paddingLeft: `${0.25 + group.depth * 0.75}rem` }}
                    >
                      {group.name}
                    </p>
                    <ul>
                      {group.files.map((file) => (
                        <li key={file.path}>
                          <button
                            type="button"
                            disabled={file.tooLarge || busy}
                            onClick={() => void loadFile(file)}
                            className={cn(
                              "flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm",
                              selectedFile?.path === file.path
                                ? "bg-primary-muted text-fg"
                                : "hover:bg-surface-raised",
                            )}
                            style={{ paddingLeft: `${0.5 + group.depth * 0.75}rem` }}
                          >
                            <span className="min-w-0">
                              <span className="block truncate">{file.name}</span>
                              <span className="block text-[11px] text-fg-subtle">
                                {formatConfigSize(file.size)} · {formatConfigTimestamp(file.modified)}
                              </span>
                            </span>
                            <span className="flex items-center gap-1">
                              <Badge tone="neutral">{file.format}</Badge>
                              {file.tooLarge ? (
                                <span className="text-[11px] text-danger">&gt;10 MiB</span>
                              ) : null}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="min-h-80">
          <CardHeader className="py-3">
            <div className="min-w-0">
              <CardTitle className="text-sm">{t("editor")}</CardTitle>
              {selectedFile ? (
                <CardDescription className="truncate font-mono">
                  {selectedFile.name}
                </CardDescription>
              ) : null}
            </div>
            {fileData ? (
              <div className="flex gap-1">
                <Button
                  type="button"
                  size="sm"
                  variant={editMode === "visual" ? "primary" : "outline"}
                  disabled={!fileData.visualSupported}
                  onClick={() => void switchMode("visual")}
                >
                  {t("visual")}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={editMode === "raw" ? "primary" : "outline"}
                  onClick={() => void switchMode("raw")}
                >
                  {t("raw")}
                </Button>
              </div>
            ) : null}
          </CardHeader>
          <CardContent className="space-y-3">
            {!selectedFile && pending !== "load-file" ? (
              <p className="py-8 text-center text-sm text-fg-subtle">{t("chooseFile")}</p>
            ) : null}
            {pending === "load-file" ? (
              <p className="py-8 text-center text-sm text-fg-subtle">{t("loadingFile")}</p>
            ) : null}
            {fileData && pending !== "load-file" ? (
              <>
                {fileData.parseError ? (
                  <p className="rounded-md border border-warn/30 bg-warn-muted/40 px-3 py-2 text-sm text-warn">
                    {fileData.parseError}
                  </p>
                ) : null}
                {editMode === "visual" ? (
                  <div className="space-y-4">
                    <Input
                      value={fieldSearch}
                      onChange={(event) => setFieldSearch(event.target.value)}
                      placeholder={t("searchFields")}
                    />
                    {fieldGroups.map((group) => (
                      <fieldset key={group.name} className="space-y-3">
                        <legend className="text-xs font-semibold uppercase tracking-wide text-fg-subtle">
                          {group.name}
                        </legend>
                        {group.fields.map((field) => (
                          <div key={field.id}>
                            <Label htmlFor={`plugin-config-field-${field.id}`}>
                              <code>{field.key}</code>
                              <span className="ml-1 text-xs font-normal text-fg-subtle">
                                {t("line")} {field.line}
                              </span>
                            </Label>
                            {field.kind === "boolean" ? (
                              <Switch
                                id={`plugin-config-field-${field.id}`}
                                label={field.key}
                                checked={fieldValues[field.id] === true}
                                onCheckedChange={(checked) =>
                                  setFieldValues((current) => ({
                                    ...current,
                                    [field.id]: checked,
                                  }))
                                }
                              />
                            ) : null}
                            {field.kind === "integer" || field.kind === "number" ? (
                              <Input
                                id={`plugin-config-field-${field.id}`}
                                type="number"
                                step={field.kind === "integer" ? "1" : "any"}
                                value={
                                  typeof fieldValues[field.id] === "number"
                                    ? String(fieldValues[field.id])
                                    : ""
                                }
                                onChange={(event) =>
                                  setFieldValues((current) => ({
                                    ...current,
                                    [field.id]: parseFieldInput(field, event.target.value),
                                  }))
                                }
                              />
                            ) : null}
                            {field.kind === "string" ? (
                              <Textarea
                                id={`plugin-config-field-${field.id}`}
                                rows={2}
                                value={String(fieldValues[field.id] ?? "")}
                                onChange={(event) =>
                                  setFieldValues((current) => ({
                                    ...current,
                                    [field.id]: event.target.value,
                                  }))
                                }
                              />
                            ) : null}
                            {field.comment ? (
                              <p className="mt-1 text-xs text-fg-subtle">{field.comment}</p>
                            ) : null}
                          </div>
                        ))}
                      </fieldset>
                    ))}
                  </div>
                ) : (
                  <Textarea
                    className="min-h-80 font-mono text-xs"
                    spellCheck={false}
                    value={rawContent}
                    onChange={(event) => setRawContent(event.target.value)}
                  />
                )}
                <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
                  {dirty ? (
                    <p className="mr-auto text-xs text-warn">{t("unsaved")}</p>
                  ) : (
                    <span className="mr-auto" />
                  )}
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => void reloadFile()}
                  >
                    <RefreshCw className="size-3.5" />
                    {t("reload")}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    disabled={busy || !dirty}
                    onClick={() => void saveFile()}
                  >
                    <Save className="size-3.5" />
                    {t("save")}
                  </Button>
                </div>
              </>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
