"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Check, Copy, CornerDownLeft } from "lucide-react";
import { breadcrumbs, resolveJumpPath } from "@/modules/files/paths";
import { Button } from "@/shared/ui/button";
import { Input, Label } from "@/shared/ui/input";

export function FilesPathBar({
  root,
  path,
  disabled,
  onGo,
}: {
  root: string;
  path: string;
  disabled: boolean;
  onGo: (next: string) => void;
}) {
  const t = useTranslations("files");
  const [draft, setDraft] = useState(path);
  const [copied, setCopied] = useState(false);
  const crumbs = breadcrumbs(root, path);

  function go() {
    onGo(resolveJumpPath(root, path, draft));
  }

  async function copyPath() {
    try {
      await navigator.clipboard.writeText(path);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="space-y-3">
      <div>
        <Label htmlFor="files-path-input">{t("currentPath")}</Label>
        <div className="flex flex-wrap gap-2">
          <Input
            id="files-path-input"
            data-testid="files-path-input"
            className="min-w-56 flex-1 font-mono text-xs"
            value={draft}
            disabled={disabled}
            spellCheck={false}
            autoComplete="off"
            placeholder={root}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                go();
              }
            }}
          />
          <Button
            type="button"
            variant="outline"
            data-testid="files-path-go"
            disabled={disabled}
            onClick={go}
          >
            <CornerDownLeft />
            {t("pathGo")}
          </Button>
          <Button
            type="button"
            variant="outline"
            data-testid="files-path-copy"
            onClick={() => void copyPath()}
          >
            {copied ? <Check /> : <Copy />}
            {copied ? t("copied") : t("copyPath")}
          </Button>
        </div>
        <p className="mt-1 font-mono text-xs text-fg-subtle break-all" data-testid="files-current-path">
          {path}
        </p>
      </div>
      <nav className="flex flex-wrap items-center gap-1 text-sm" aria-label={t("currentPath")}>
        {crumbs.map((crumb, index) => (
          <span key={crumb.path} className="flex items-center gap-1">
            {index > 0 ? <span className="text-fg-subtle">/</span> : null}
            {index === crumbs.length - 1 ? (
              <span className="font-medium text-fg">{crumb.name}</span>
            ) : (
              <button
                type="button"
                className="text-primary hover:underline"
                disabled={disabled}
                onClick={() => onGo(crumb.path)}
              >
                {crumb.name}
              </button>
            )}
          </span>
        ))}
      </nav>
    </div>
  );
}
