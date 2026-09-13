"use client";

import { useFormatter, useTranslations } from "next-intl";
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
  formatConfigSize,
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
import {
  EMPTY_RUNTIME,
  parseFieldInput,
  type Banner,
  type SourceRuntime,
} from "@/modules/plugin-configs/plugin-config-helpers";
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

export type PluginConfigsListingProps = {
  workspace: PluginConfigWorkspace;
  runtime: Record<number, SourceRuntime>;
  activeSourceId: number | null;
  activeSource: PluginConfigSource | null;
  activeRuntime: SourceRuntime;
  showAddSource: boolean;
  sourcePath: string;
  showBrowser: boolean;
  browsing: boolean;
  browsePath: string;
  browseItems: readonly PluginConfigBrowseItem[];
  fileSearch: string;
  fieldSearch: string;
  selectedFile: PluginConfigScannedFile | null;
  fileData: PluginConfigFile | null;
  editMode: PluginConfigEditMode;
  fieldValues: Record<string, PluginConfigFieldValue>;
  rawContent: string;
  pending: string | null;
  banner: Banner | null;
  dirty: boolean;
  fileGroups: ReturnType<typeof groupConfigFiles>;
  fieldGroups: ReturnType<typeof groupConfigFields>;
  busy: boolean;
  setShowAddSource: (value: boolean | ((open: boolean) => boolean)) => void;
  setSourcePath: (value: string) => void;
  setFileSearch: (value: string) => void;
  setFieldSearch: (value: string) => void;
  setFieldValues: (
    value:
      | Record<string, PluginConfigFieldValue>
      | ((current: Record<string, PluginConfigFieldValue>) => Record<string, PluginConfigFieldValue>),
  ) => void;
  setRawContent: (value: string) => void;
  refreshSources: () => Promise<void> | void;
  restoreDefault: () => Promise<void> | void;
  addSource: () => Promise<void> | void;
  openBrowser: () => Promise<void> | void;
  browseUp: () => void;
  chooseBrowsePath: (path: string) => void;
  browse: (path: string) => Promise<void> | void;
  selectSource: (source: PluginConfigSource) => Promise<void> | void;
  loadSource: (source: PluginConfigSource) => Promise<void> | void;
  removeSource: (source: PluginConfigSource) => Promise<void> | void;
  loadFile: (file: PluginConfigScannedFile) => Promise<void> | void;
  switchMode: (mode: PluginConfigEditMode) => Promise<void> | void;
  reloadFile: () => Promise<void> | void;
  saveFile: () => Promise<void> | void;
};

export function PluginConfigsListing(props: PluginConfigsListingProps) {
  const t = useTranslations("pluginConfigs");
  const format = useFormatter();
  const {
    workspace,
    runtime,
    activeSourceId,
    activeSource,
    activeRuntime,
    showAddSource,
    sourcePath,
    showBrowser,
    browsing,
    browsePath,
    browseItems,
    fileSearch,
    fieldSearch,
    selectedFile,
    fileData,
    editMode,
    fieldValues,
    rawContent,
    pending,
    banner,
    dirty,
    fileGroups,
    fieldGroups,
    busy,
    setShowAddSource,
    setSourcePath,
    setFileSearch,
    setFieldSearch,
    setFieldValues,
    setRawContent,
    refreshSources,
    restoreDefault,
    addSource,
    openBrowser,
    browseUp,
    chooseBrowsePath,
    browse,
    selectSource,
    loadSource,
    removeSource,
    loadFile,
    switchMode,
    reloadFile,
    saveFile,
  } = props;
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
                                {formatConfigSize(file.size)} · {file.modified
                                  ? format.dateTime(file.modified * 1000, {
                                      dateStyle: "medium",
                                      timeStyle: "medium",
                                    })
                                  : "—"}
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
