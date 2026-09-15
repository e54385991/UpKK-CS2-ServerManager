"use client";

import { useTranslations } from "next-intl";
import {
  ArrowUp,
  ClipboardCopy,
  ClipboardPaste,
  FolderInput,
  FolderPlus,
  FolderUp,
  RefreshCw,
  Scissors,
  Search,
  Trash2,
  TriangleAlert,
  Upload,
  X,
} from "lucide-react";
import dynamic from "next/dynamic";
import { ExtractDialog, FileEditorDialog, MoveDialog, RenameDialog } from "@/modules/files/lazy-dialogs";
import { FilesPathBar } from "@/modules/files/path-bar";
import { FilesShortcuts } from "@/modules/files/files-shortcuts";
import { isAtRoot, parentWithinRoot } from "@/modules/files/paths";
import { FILE_KIND_FILTERS, isArchiveFile, isTextFile, type FilesWorkspace } from "@/modules/files/types";
import { useFilesWorkspace } from "@/modules/files/use-files-workspace";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Input, Label } from "@/shared/ui/input";
import { cn } from "@/shared/lib/cn";

const FilesUploadDock = dynamic(() =>
  import("@/modules/files/files-upload-dock").then((mod) => mod.FilesUploadDock),
);
const FilesListing = dynamic(
  () => import("@/modules/files/files-listing").then((mod) => mod.FilesListing),
  {
    loading: () => (
      <div
        data-testid="files-dropzone"
        className="min-h-64 rounded-lg bg-surface"
        aria-busy="true"
      />
    ),
  },
);

export function FilesConsole({ initial }: { initial: FilesWorkspace }) {
  const t = useTranslations("files");
  const {
    workspace,
    pending,
    banner,
    folderName,
    setFolderName,
    renameFrom,
    setRenameFrom,
    moveOpen,
    setMoveOpen,
    editing,
    urlForm,
    setUrlForm,
    urlTaskId,
    extractEntry,
    setExtractEntry,
    query,
    setQuery,
    kind,
    setKind,
    sortKey,
    sortDir,
    selected,
    setSelected,
    dragOver,
    setDragOver,
    serverId,
    clipboard,
    canMutate,
    listedFiles,
    totalFiles,
    filtering,
    allVisibleSelected,
    copiedEntry,
    uploadRef,
    folderRef,
    bindFolderInput,
    listAnchorRef,
    searchRef,
    selectAllRef,
    load,
    stageClipboard,
    pasteItems,
    toggleSort,
    run,
    toggleSelect,
    onDragEnter,
    onDrop,
    onUpload,
    deleteSelected,
    download,
    openEditor,
    openExtract,
    copyEntryPath,
    openMove,
    cancelUpload,
    removeEntry,
    createDirectory,
    rename,
    saveEdit,
    startUrl,
    onMoveStarted,
    onExtractStarted,
    closeEditor,
  } = useFilesWorkspace(initial);

  return (
    <div className="space-y-6">
      {banner ? (
        <p
          className={cn(
            "rounded-lg border px-4 py-3 text-sm",
            banner.tone === "ok" && "border-ok/30 bg-ok-muted/40 text-ok",
            banner.tone === "warn" && "border-warn/30 bg-warn-muted/40 text-warn",
            banner.tone === "danger" && "border-danger/30 bg-danger-muted/40 text-danger",
          )}
        >
          {banner.text}
        </p>
      ) : null}

      {!workspace.sshOk ? (
        <Card className="border-danger/30 bg-danger-muted/20">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-danger">
              <TriangleAlert className="size-4" />
              {t("sshDown")}
            </CardTitle>
            <CardDescription>{workspace.sshError || t("sshDownHelp")}</CardDescription>
          </CardHeader>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("title")}</CardTitle>
            <CardDescription>{t("help")}</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <FilesPathBar
            key={workspace.path}
            root={workspace.root}
            path={workspace.path}
            disabled={Boolean(pending)}
            onGo={(next) => void load(next)}
          />

          <FilesShortcuts
            serverId={serverId}
            root={workspace.root}
            path={workspace.path}
            disabled={Boolean(pending)}
            onGo={(next) => void load(next)}
          />

          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-48 flex-1">
              <Label htmlFor="new-folder">{t("newFolder")}</Label>
              <Input
                id="new-folder"
                value={folderName}
                disabled={!canMutate}
                onChange={(event) => setFolderName(event.target.value)}
                placeholder={t("folderName")}
              />
            </div>
            <Button
              type="button"
              disabled={!canMutate || !folderName.trim()}
              onClick={() => void run("mkdir", createDirectory)}
            >
              <FolderPlus />
              {pending === "mkdir" ? t("creating") : t("createFolder")}
            </Button>
          </div>

          {!isAtRoot(workspace.root, workspace.path) ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              data-testid="files-list-parent"
              disabled={Boolean(pending)}
              onClick={() => void load(parentWithinRoot(workspace.root, workspace.path))}
            >
              <ArrowUp />
              {t("parent")}
            </Button>
          ) : null}

          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative min-w-48 flex-1">
                <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-fg-subtle" />
                <Input
                  ref={searchRef}
                  id="files-search"
                  data-testid="files-search"
                  className="pr-10 pl-9"
                  value={query}
                  disabled={!workspace.sshOk}
                  spellCheck={false}
                  autoComplete="off"
                  placeholder={t("searchPlaceholder")}
                  aria-label={t("search")}
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key !== "Enter" || listedFiles.length !== 1) return;
                    event.preventDefault();
                    const only = listedFiles[0];
                    if (!only) return;
                    if (only.type === "directory") {
                      void load(only.path);
                      return;
                    }
                    if (isArchiveFile(only.name)) {
                      void openExtract(only);
                      return;
                    }
                    if (isTextFile(only.name)) void openEditor(only);
                  }}
                />
                {query ? (
                  <button
                    type="button"
                    className="absolute top-1/2 right-2 -translate-y-1/2 rounded-md p-1 text-fg-subtle hover:bg-surface-overlay hover:text-fg"
                    aria-label={t("searchClear")}
                    onClick={() => {
                      setQuery("");
                      searchRef.current?.focus();
                    }}
                  >
                    <X className="size-4" />
                  </button>
                ) : null}
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                data-testid="files-refresh"
                disabled={Boolean(pending)}
                onClick={() => void load(workspace.path)}
              >
                <RefreshCw />
                {t("refresh")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                data-testid="files-upload"
                disabled={!canMutate}
                onClick={() => uploadRef.current?.click()}
              >
                <Upload />
                {pending === "upload" ? t("uploading") : t("uploadFiles")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                data-testid="files-upload-folder"
                disabled={!canMutate}
                onClick={() => folderRef.current?.click()}
              >
                <FolderUp />
                {t("uploadFolder")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                data-testid="files-copy-items"
                disabled={selected.size === 0}
                title={t("copyItemsHint")}
                onClick={() => stageClipboard([...selected], "copy")}
              >
                <ClipboardCopy />
                {t("copyItems")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                data-testid="files-paste"
                disabled={!canMutate || clipboard.length === 0}
                title={t("pasteItemsHint")}
                onClick={() => void pasteItems()}
              >
                <ClipboardPaste />
                {t("pasteItems")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                data-testid="files-cut-items"
                disabled={selected.size === 0}
                title={t("cutItemsHint")}
                onClick={() => stageClipboard([...selected], "move")}
              >
                <Scissors />
                {t("cutItems")}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                data-testid="files-move-items"
                disabled={!canMutate || selected.size === 0}
                onClick={() => openMove()}
              >
                <FolderInput />
                {t("move")}
              </Button>
              <input
                ref={uploadRef}
                type="file"
                className="hidden"
                multiple
                onChange={(event) => void onUpload(event)}
              />
              <input
                ref={bindFolderInput}
                type="file"
                className="hidden"
                multiple
                onChange={(event) => void onUpload(event)}
              />
            </div>
            {selected.size > 0 ? (
              <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface-overlay px-3 py-2 text-sm">
                <span>{t("selectedCount", { count: selected.size })}</span>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setSelected(new Set())}
                >
                  {t("clearSelection")}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="danger"
                  disabled={!canMutate}
                  onClick={() => void deleteSelected()}
                >
                  <Trash2 />
                  {t("removeSelected")}
                </Button>
              </div>
            ) : null}
            <FilesUploadDock onCancel={cancelUpload} />
            <div className="flex flex-wrap items-center gap-1">
              {FILE_KIND_FILTERS.map((id) => (
                <Button
                  key={id}
                  type="button"
                  size="sm"
                  variant={kind === id ? "secondary" : "ghost"}
                  disabled={!workspace.sshOk}
                  onClick={() => setKind(id)}
                >
                  {t(`kind.${id}`)}
                </Button>
              ))}
              <span className="ml-auto text-xs text-fg-subtle">
                {filtering
                  ? t("searchCount", { shown: listedFiles.length, total: totalFiles })
                  : t("entryCount", { count: totalFiles })}
              </span>
            </div>
          </div>

          <FilesListing
            workspace={workspace}
            pending={pending}
            dragOver={dragOver}
            canMutate={canMutate}
            listedFiles={listedFiles}
            totalFiles={totalFiles}
            query={query}
            selected={selected}
            sortKey={sortKey}
            sortDir={sortDir}
            copiedEntry={copiedEntry}
            allVisibleSelected={allVisibleSelected}
            listAnchorRef={listAnchorRef}
            selectAllRef={selectAllRef}
            onDragEnter={onDragEnter}
            onDrop={onDrop}
            onDragOverChange={setDragOver}
            toggleSort={toggleSort}
            toggleSelect={toggleSelect}
            setSelected={setSelected}
            load={load}
            stageClipboard={stageClipboard}
            copyEntryPath={copyEntryPath}
            download={download}
            openEditor={openEditor}
            openExtract={openExtract}
            onRename={setRenameFrom}
            onRemove={removeEntry}
          />
        </CardContent>
      </Card>

      {extractEntry ? (
        <ExtractDialog
          serverId={serverId}
          entry={extractEntry}
          destination={workspace.path}
          onClose={() => setExtractEntry(null)}
          onStarted={(operation) => onExtractStarted(operation)}
        />
      ) : null}

      {renameFrom ? (
        <RenameDialog
          entry={renameFrom}
          busy={pending === "rename"}
          onClose={() => setRenameFrom(null)}
          onSubmit={rename}
        />
      ) : null}

      {moveOpen ? (
        <MoveDialog
          serverId={serverId}
          sources={[...selected]}
          destination={workspace.path}
          onClose={() => setMoveOpen(false)}
          onStarted={onMoveStarted}
        />
      ) : null}

      {editing ? (
        <FileEditorDialog
          file={editing}
          busy={pending === "save-edit"}
          onClose={closeEditor}
          onSave={saveEdit}
        />
      ) : null}

      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("urlTitle")}</CardTitle>
            <CardDescription>{t("urlHelp")}</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div>
            <Label htmlFor="url-download">{t("url")}</Label>
            <Input
              id="url-download"
              value={urlForm.url}
              disabled={!canMutate || Boolean(urlTaskId)}
              onChange={(event) => setUrlForm({ ...urlForm, url: event.target.value })}
              placeholder="https://"
            />
          </div>
          <div>
            <Label htmlFor="url-name">{t("urlFilename")}</Label>
            <Input
              id="url-name"
              value={urlForm.filename}
              disabled={!canMutate || Boolean(urlTaskId)}
              onChange={(event) =>
                setUrlForm({ ...urlForm, filename: event.target.value })
              }
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-fg-muted">
            <input
              type="checkbox"
              checked={urlForm.overwrite}
              disabled={!canMutate || Boolean(urlTaskId)}
              onChange={(event) =>
                setUrlForm({ ...urlForm, overwrite: event.target.checked })
              }
              className="size-4 accent-primary"
            />
            {t("overwrite")}
          </label>
          <Button
            type="button"
            disabled={!canMutate || !urlForm.url.trim() || Boolean(urlTaskId)}
            onClick={() => void run("url", startUrl)}
          >
            {urlTaskId ? t("urlRunning") : t("startUrl")}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
