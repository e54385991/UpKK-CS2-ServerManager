"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import {
  listRegisterReleasesAction,
  registerManagedPluginAction,
} from "@/modules/updates/actions";
import type {
  ManagedPluginSourceType,
  ManagedUpdatePlugin,
  RegisterMarketOption,
  RegisterRelease,
} from "@/modules/updates/types";
import { Button } from "@/shared/ui/button";
import { Input, Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";
import { Textarea } from "@/shared/ui/textarea";

function splitLines(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function guessAssetGlob(tagName: string, assetName: string): string {
  let pattern = assetName;
  for (const value of [tagName, tagName.replace(/^v/i, "")].filter(Boolean)) {
    pattern = pattern.replace(
      new RegExp(value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "ig"),
      "*",
    );
  }
  return pattern;
}

const FRAMEWORKS = [
  { value: "metamod", label: "Metamod:Source" },
  { value: "counterstrikesharp", label: "CounterStrikeSharp" },
] as const;

export function PluginRegisterForm({
  serverId,
  marketOptions,
  pending,
  onPending,
  onBanner,
  onRegistered,
}: {
  serverId: number;
  marketOptions: readonly RegisterMarketOption[];
  pending: string | null;
  onPending: (value: string | null) => void;
  onBanner: (value: string | null) => void;
  onRegistered: (plugin: ManagedUpdatePlugin) => void;
}) {
  const t = useTranslations("pluginUpdates");
  const [sourceType, setSourceType] = useState<ManagedPluginSourceType>("github");
  const [frameworkKey, setFrameworkKey] = useState("metamod");
  const [marketPluginId, setMarketPluginId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [releaseIndex, setReleaseIndex] = useState("");
  const [assetIndex, setAssetIndex] = useState("");
  const [assetGlob, setAssetGlob] = useState("");
  const [installPath, setInstallPath] = useState("");
  const [excludeDirs, setExcludeDirs] = useState("");
  const [excludeFiles, setExcludeFiles] = useState("");
  const [releases, setReleases] = useState<readonly RegisterRelease[]>([]);

  function resetGithubFields() {
    setReleaseIndex("");
    setAssetIndex("");
    setAssetGlob("");
    setReleases([]);
  }

  function onSourceChanged(next: ManagedPluginSourceType) {
    setSourceType(next);
    setMarketPluginId("");
    setDisplayName("");
    setRepoUrl("");
    setInstallPath("");
    setExcludeDirs("");
    setExcludeFiles("");
    resetGithubFields();
  }

  function onMarketChanged(value: string) {
    setMarketPluginId(value);
    const plugin = marketOptions.find((item) => String(item.id) === value);
    if (!plugin) {
      setDisplayName("");
      setRepoUrl("");
      resetGithubFields();
      return;
    }
    setDisplayName(plugin.title);
    setRepoUrl(plugin.githubUrl);
    resetGithubFields();
  }

  async function fetchReleases() {
    if (!repoUrl.trim()) return;
    onPending("fetch-releases");
    onBanner(null);
    const result = await listRegisterReleasesAction(repoUrl.trim(), serverId);
    onPending(null);
    if (!result.ok) {
      onBanner(result.error || t("failed"));
      return;
    }
    setReleases(result.data);
    setReleaseIndex("");
    setAssetIndex("");
  }

  function onReleaseChanged(value: string) {
    setReleaseIndex(value);
    setAssetIndex("");
    if (value === "unknown") {
      return;
    }
    setAssetGlob("");
  }

  function onAssetChanged(value: string) {
    setAssetIndex(value);
    const release = releases[Number(releaseIndex)];
    const asset = release?.assets[Number(value)];
    if (!release || !asset) return;
    setAssetGlob(guessAssetGlob(release.tagName, asset.name));
    if (!displayName.trim()) {
      const guessed = repoUrl.split("/").filter(Boolean).pop() || "";
      if (guessed) setDisplayName(guessed);
    }
  }

  async function register() {
    onPending("register");
    onBanner(null);
    if (sourceType === "framework") {
      const result = await registerManagedPluginAction(serverId, {
        sourceType: "framework",
        displayName: frameworkKey,
        frameworkKey,
      });
      onPending(null);
      if (!result.ok) {
        onBanner(result.error || t("failed"));
        return;
      }
      onRegistered(result.data);
      onBanner(t("registered"));
      return;
    }

    const unknownVersion = releaseIndex === "unknown";
    const release = unknownVersion ? null : releases[Number(releaseIndex)];
    const asset = unknownVersion ? null : release?.assets[Number(assetIndex)];
    if ((!unknownVersion && (!release || !asset)) || !assetGlob.trim()) {
      onPending(null);
      onBanner(t("selectReleaseAsset"));
      return;
    }

    const result = await registerManagedPluginAction(serverId, {
      sourceType,
      displayName: displayName.trim() || repoUrl.split("/").filter(Boolean).pop() || sourceType,
      repoUrl: repoUrl.trim() || null,
      marketPluginId:
        sourceType === "market" && marketPluginId ? Number(marketPluginId) : null,
      installedReleaseId: unknownVersion ? null : release?.id ?? null,
      installedVersion: unknownVersion ? "unknown" : release?.tagName,
      assetGlob: assetGlob.trim(),
      customInstallPath: installPath.trim() || null,
      excludeDirs: splitLines(excludeDirs),
      excludeFiles: splitLines(excludeFiles),
    });
    onPending(null);
    if (!result.ok) {
      onBanner(result.error || t("failed"));
      return;
    }
    onRegistered(result.data);
    onBanner(t("registered"));
    onSourceChanged(sourceType);
  }

  return (
    <div className="space-y-4" data-testid="plugin-register-form">
      <div>
        <p className="text-sm font-medium text-fg">{t("registerTitle")}</p>
        <p className="mt-1 text-xs text-fg-subtle">{t("registerHelp")}</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="register-source">{t("source")}</Label>
          <Select
            id="register-source"
            value={sourceType}
            onChange={(event) =>
              onSourceChanged(event.target.value as ManagedPluginSourceType)
            }
          >
            <option value="github">{t("sourceGithub")}</option>
            <option value="market">{t("sourceMarket")}</option>
            <option value="framework">{t("sourceFramework")}</option>
          </Select>
        </div>
        {sourceType === "framework" ? (
          <div className="space-y-1.5">
            <Label htmlFor="register-framework">{t("framework")}</Label>
            <Select
              id="register-framework"
              value={frameworkKey}
              onChange={(event) => setFrameworkKey(event.target.value)}
            >
              {FRAMEWORKS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </Select>
          </div>
        ) : null}
        {sourceType === "market" ? (
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="register-market">{t("marketPlugin")}</Label>
            <Select
              id="register-market"
              value={marketPluginId}
              onChange={(event) => onMarketChanged(event.target.value)}
            >
              <option value="">--</option>
              {marketOptions.map((plugin) => (
                <option key={plugin.id} value={plugin.id}>
                  {plugin.title}
                  {plugin.version ? ` · ${plugin.version}` : ""}
                </option>
              ))}
            </Select>
          </div>
        ) : null}
      </div>
      {sourceType !== "framework" ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="register-name">{t("name")}</Label>
            <Input
              id="register-name"
              value={displayName}
              disabled={sourceType === "market"}
              onChange={(event) => setDisplayName(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="register-repo">{t("repoUrl")}</Label>
            <div className="flex gap-2">
              <Input
                id="register-repo"
                value={repoUrl}
                disabled={sourceType === "market"}
                placeholder="https://github.com/owner/repo"
                onChange={(event) => setRepoUrl(event.target.value)}
              />
              <Button
                type="button"
                variant="outline"
                disabled={Boolean(pending) || !repoUrl.trim()}
                onClick={() => void fetchReleases()}
              >
                {pending === "fetch-releases" ? t("fetching") : t("fetch")}
              </Button>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="register-release">{t("installedRelease")}</Label>
            <Select
              id="register-release"
              value={releaseIndex}
              onChange={(event) => onReleaseChanged(event.target.value)}
            >
              <option value="">--</option>
              <option value="unknown">{t("unknownVersion")}</option>
              {releases.map((release, index) => (
                <option key={release.id ?? release.tagName} value={index}>
                  {release.tagName}
                </option>
              ))}
            </Select>
          </div>
          {releaseIndex !== "unknown" ? (
            <div className="space-y-1.5">
              <Label htmlFor="register-asset">{t("asset")}</Label>
              <Select
                id="register-asset"
                value={assetIndex}
                onChange={(event) => onAssetChanged(event.target.value)}
              >
                <option value="">--</option>
                {(releases[Number(releaseIndex)]?.assets ?? []).map((asset, index) => (
                  <option key={asset.name} value={index}>
                    {asset.name}
                  </option>
                ))}
              </Select>
            </div>
          ) : null}
          <div className="space-y-1.5">
            <Label htmlFor="register-glob">{t("assetGlob")}</Label>
            <Input
              id="register-glob"
              className="font-mono"
              value={assetGlob}
              onChange={(event) => setAssetGlob(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="register-path">{t("installPath")}</Label>
            <Input
              id="register-path"
              value={installPath}
              placeholder="addons"
              onChange={(event) => setInstallPath(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="register-exclude-dirs">{t("excludeDirs")}</Label>
            <Textarea
              id="register-exclude-dirs"
              rows={3}
              value={excludeDirs}
              placeholder={t("excludeDirsHint")}
              onChange={(event) => setExcludeDirs(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="register-exclude-files">{t("excludeFiles")}</Label>
            <Textarea
              id="register-exclude-files"
              rows={3}
              value={excludeFiles}
              placeholder={t("excludeFilesHint")}
              onChange={(event) => setExcludeFiles(event.target.value)}
            />
          </div>
        </div>
      ) : null}
      <Button
        type="button"
        disabled={Boolean(pending)}
        onClick={() => void register()}
      >
        {pending === "register" ? t("registering") : t("register")}
      </Button>
    </div>
  );
}
