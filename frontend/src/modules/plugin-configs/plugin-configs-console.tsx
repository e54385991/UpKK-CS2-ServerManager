"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import {
  browsePluginConfigPathAction,
  createPluginConfigSourceAction,
  deletePluginConfigSourceAction,
  getPluginConfigFileAction,
  listPluginConfigSourcesAction,
  restoreDefaultPluginConfigSourcesAction,
  savePluginConfigFileAction,
} from "@/modules/plugin-configs/actions";
import { PluginConfigsListing } from "@/modules/plugin-configs/plugin-configs-listing";
import {
  EMPTY_RUNTIME,
  toScannedFile,
  valuesFromFile,
  type Banner,
  type ScanEvent,
  type SourceRuntime,
} from "@/modules/plugin-configs/plugin-config-helpers";
import {
  groupConfigFields,
  groupConfigFiles,
  type PluginConfigBrowseItem,
  type PluginConfigEditMode,
  type PluginConfigFieldValue,
  type PluginConfigFile,
  type PluginConfigScannedFile,
  type PluginConfigSource,
  type PluginConfigWorkspace,
} from "@/modules/plugin-configs/types";
import { confirm } from "@/shared/feedback";

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
    <PluginConfigsListing
      workspace={workspace}
      runtime={runtime}
      activeSourceId={activeSourceId}
      activeSource={activeSource}
      activeRuntime={activeRuntime}
      showAddSource={showAddSource}
      sourcePath={sourcePath}
      showBrowser={showBrowser}
      browsing={browsing}
      browsePath={browsePath}
      browseItems={browseItems}
      fileSearch={fileSearch}
      fieldSearch={fieldSearch}
      selectedFile={selectedFile}
      fileData={fileData}
      editMode={editMode}
      fieldValues={fieldValues}
      rawContent={rawContent}
      pending={pending}
      banner={banner}
      dirty={dirty}
      fileGroups={fileGroups}
      fieldGroups={fieldGroups}
      busy={busy}
      setShowAddSource={setShowAddSource}
      setSourcePath={setSourcePath}
      setFileSearch={setFileSearch}
      setFieldSearch={setFieldSearch}
      setFieldValues={setFieldValues}
      setRawContent={setRawContent}
      refreshSources={refreshSources}
      restoreDefault={restoreDefault}
      addSource={addSource}
      openBrowser={openBrowser}
      browseUp={browseUp}
      chooseBrowsePath={chooseBrowsePath}
      browse={browse}
      selectSource={selectSource}
      loadSource={loadSource}
      removeSource={removeSource}
      loadFile={loadFile}
      switchMode={switchMode}
      reloadFile={reloadFile}
      saveFile={saveFile}
    />
  );
}
