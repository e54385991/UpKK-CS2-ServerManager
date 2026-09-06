"use client";

import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { LoaderCircle, WandSparkles } from "lucide-react";
import {
  fetchMarketRepoInfoAction,
  listPluginDependencyOptionsAction,
  updateMarketPluginAction,
} from "@/modules/plugins/actions";
import {
  PLUGIN_CATEGORIES,
  PLUGIN_FRAMEWORKS,
  isPluginCategory,
  isPluginFramework,
  type MarketPlugin,
  type MarketPluginUpdateInput,
  type PluginCategory,
  type PluginDependencyOptions,
  type PluginFramework,
} from "@/modules/plugins/types";
import { notify } from "@/shared/feedback/notify";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Input, Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";
import { Textarea } from "@/shared/ui/textarea";

type PendingAction = "repo-info" | "dependencies" | "save" | null;

type FormState = {
  title: string;
  description: string;
  author: string;
  version: string;
  category: PluginCategory;
  framework: PluginFramework;
  iconUrl: string;
  tags: string;
  customInstallPath: string;
  isRecommended: boolean;
  dependencyIds: readonly number[];
};

function initialState(plugin: MarketPlugin): FormState {
  return {
    title: plugin.title,
    description: plugin.description ?? "",
    author: plugin.author ?? "",
    version: plugin.version ?? "",
    category: isPluginCategory(plugin.category) ? plugin.category : "other",
    framework: plugin.framework,
    iconUrl: plugin.iconUrl ?? "",
    tags: plugin.tags ?? "",
    customInstallPath: plugin.customInstallPath ?? "",
    isRecommended: plugin.isRecommended,
    dependencyIds: plugin.dependencies.map((dep) => dep.id),
  };
}

/**
 * Build the PATCH payload from what actually changed, so an edit never
 * overwrites a field the administrator did not touch.
 */
function changedFields(
  plugin: MarketPlugin,
  form: FormState,
): MarketPluginUpdateInput {
  const base = initialState(plugin);
  const input: Record<string, unknown> = {};
  if (form.title.trim() !== base.title) input.title = form.title.trim();
  if (form.description !== base.description) input.description = form.description;
  if (form.author.trim() !== base.author) input.author = form.author.trim();
  if (form.version.trim() !== base.version) input.version = form.version.trim();
  if (form.category !== base.category) input.category = form.category;
  if (form.framework !== base.framework) input.framework = form.framework;
  if (form.iconUrl.trim() !== base.iconUrl) input.iconUrl = form.iconUrl.trim();
  if (form.tags.trim() !== base.tags) input.tags = form.tags.trim();
  if (form.customInstallPath.trim() !== base.customInstallPath) {
    input.customInstallPath = form.customInstallPath.trim();
  }
  if (form.isRecommended !== base.isRecommended) {
    input.isRecommended = form.isRecommended;
  }
  const before = [...base.dependencyIds].sort().join(",");
  const after = [...form.dependencyIds].sort().join(",");
  if (before !== after) input.dependencyIds = form.dependencyIds;
  return input as MarketPluginUpdateInput;
}

/**
 * Mounted only while open (see `MarketPluginEditButton`), so the form state is
 * seeded from the current listing on every open without a reset effect.
 */
export function MarketPluginEditDialog({
  plugin,
  open,
  onClose,
}: {
  plugin: MarketPlugin;
  open: boolean;
  onClose: () => void;
}) {
  const t = useTranslations("plugins");
  const router = useRouter();
  const [form, setForm] = useState<FormState>(() => initialState(plugin));
  const [dependencySearch, setDependencySearch] = useState("");
  const [dependencyOptions, setDependencyOptions] = useState<
    readonly PluginDependencyOptions[]
  >([]);
  const [pending, setPending] = useState<PendingAction>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setPending("dependencies");
      void listPluginDependencyOptionsAction(dependencySearch).then((result) => {
        if (cancelled) return;
        setPending(null);
        if (!result.ok) {
          setError(result.error || t("create.dependencyError"));
          return;
        }
        setDependencyOptions(result.data.filter((item) => item.id !== plugin.id));
      });
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [dependencySearch, open, plugin.id, t]);

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function handleClose() {
    if (pending) return;
    onClose();
  }

  async function pullDescription() {
    setPending("repo-info");
    setError(null);
    setNotice(null);
    const result = await fetchMarketRepoInfoAction(plugin.githubUrl);
    setPending(null);
    if (!result.ok || !result.data.success) {
      setError(
        (result.ok ? result.data.error : result.error) || t("create.autoFillFailed"),
      );
      return;
    }
    const body = result.data.readme || result.data.description;
    if (!body) {
      setError(t("edit.noReadme"));
      return;
    }
    update("description", body);
    setNotice(
      result.data.readme
        ? t("create.autoFillReadmeSuccess")
        : t("create.autoFillSuccess"),
    );
  }

  function selectedDependencies(event: ChangeEvent<HTMLSelectElement>) {
    update(
      "dependencyIds",
      Array.from(event.target.selectedOptions, (option) => Number(option.value)),
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!form.title.trim()) {
      setError(t("edit.titleRequired"));
      return;
    }
    const input = changedFields(plugin, form);
    if (Object.keys(input).length === 0) {
      setError(t("edit.noChanges"));
      return;
    }
    setPending("save");
    setError(null);
    setNotice(null);
    const result = await updateMarketPluginAction(plugin.id, input);
    setPending(null);
    if (!result.ok) {
      setError(
        result.status === 403
          ? t("edit.forbidden")
          : result.error || t("edit.failed"),
      );
      return;
    }
    notify.success(t("edit.success", { name: result.data.title }));
    onClose();
    router.refresh();
  }

  return (
    <Dialog
      open={open}
      title={t("edit.title", { name: plugin.title })}
      description={t("edit.description")}
      closeLabel={t("create.cancel")}
      onClose={handleClose}
      className="max-w-4xl"
    >
      <form
        className="space-y-5"
        data-testid="market-edit-form"
        onSubmit={(event) => void submit(event)}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="market-edit-title">{t("create.titleField")} *</Label>
            <Input
              id="market-edit-title"
              value={form.title}
              required
              maxLength={255}
              onChange={(event) => update("title", event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="market-edit-author">{t("create.author")}</Label>
            <Input
              id="market-edit-author"
              value={form.author}
              maxLength={255}
              onChange={(event) => update("author", event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="market-edit-version">{t("create.version")}</Label>
            <Input
              id="market-edit-version"
              value={form.version}
              maxLength={50}
              onChange={(event) => update("version", event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="market-edit-category">{t("create.category")} *</Label>
            <Select
              id="market-edit-category"
              value={form.category}
              required
              onChange={(event) => {
                if (isPluginCategory(event.target.value)) {
                  update("category", event.target.value);
                }
              }}
            >
              {PLUGIN_CATEGORIES.map((value) => (
                <option key={value} value={value}>
                  {t(`categories.${value}`)}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="market-edit-framework">{t("create.framework")} *</Label>
            <Select
              id="market-edit-framework"
              value={form.framework}
              required
              data-testid="market-edit-framework"
              onChange={(event) => {
                if (isPluginFramework(event.target.value)) {
                  update("framework", event.target.value);
                }
              }}
            >
              {PLUGIN_FRAMEWORKS.map((value) => (
                <option key={value} value={value}>
                  {t(`frameworks.${value}`)}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="market-edit-icon">{t("create.iconUrl")}</Label>
            <Input
              id="market-edit-icon"
              type="url"
              value={form.iconUrl}
              maxLength={500}
              onChange={(event) => update("iconUrl", event.target.value)}
            />
          </div>
        </div>

        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Label htmlFor="market-edit-description">
              {t("create.descriptionField")}
            </Label>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              data-testid="market-edit-pull-readme"
              disabled={pending !== null}
              onClick={() => void pullDescription()}
            >
              {pending === "repo-info" ? (
                <LoaderCircle className="animate-spin" />
              ) : (
                <WandSparkles />
              )}
              {pending === "repo-info"
                ? t("create.autoFilling")
                : t("edit.pullReadme")}
            </Button>
          </div>
          <Textarea
            id="market-edit-description"
            value={form.description}
            rows={10}
            maxLength={10000}
            aria-describedby="market-edit-description-hint"
            onChange={(event) => update("description", event.target.value)}
          />
          <p id="market-edit-description-hint" className="text-xs text-fg-subtle">
            {t("create.descriptionMarkdownHint")}
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="market-edit-tags">{t("create.tags")}</Label>
            <Input
              id="market-edit-tags"
              value={form.tags}
              onChange={(event) => update("tags", event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="market-edit-install-path">
              {t("create.customInstallPath")}
            </Label>
            <Input
              id="market-edit-install-path"
              value={form.customInstallPath}
              maxLength={255}
              placeholder={t("create.customInstallPathPlaceholder")}
              onChange={(event) => update("customInstallPath", event.target.value)}
            />
            <p className="text-xs text-fg-subtle">{t("edit.installPathHint")}</p>
          </div>
        </div>

        <div className="space-y-2 rounded-lg border border-line bg-surface-overlay p-3">
          <Label htmlFor="market-edit-dependency-search">
            {t("create.dependencies")}
          </Label>
          <Input
            id="market-edit-dependency-search"
            value={dependencySearch}
            placeholder={t("create.dependencySearch")}
            onChange={(event) => setDependencySearch(event.target.value)}
          />
          <Select
            multiple
            size={8}
            value={form.dependencyIds.map(String)}
            aria-label={t("create.dependencies")}
            data-testid="market-edit-dependencies"
            className="h-auto min-h-40 py-2"
            disabled={pending === "dependencies"}
            onChange={selectedDependencies}
          >
            {dependencyOptions.map((option) => (
              <option key={option.id} value={option.id}>
                {option.title}
              </option>
            ))}
          </Select>
          <p className="text-xs text-fg-subtle">{t("create.dependenciesHint")}</p>
        </div>

        <label className="flex items-start gap-2 text-sm text-fg-muted">
          <input
            type="checkbox"
            checked={form.isRecommended}
            className="mt-0.5 size-4 rounded border-line accent-primary"
            onChange={(event) => update("isRecommended", event.target.checked)}
          />
          <span>{t("create.recommended")}</span>
        </label>

        {notice ? (
          <p className="text-sm text-ok" role="status" aria-live="polite">
            {notice}
          </p>
        ) : null}
        {error ? (
          <p className="text-sm text-danger" role="alert">
            {error}
          </p>
        ) : null}

        <div className="flex justify-end gap-2 border-t border-line pt-4">
          <Button
            type="button"
            variant="ghost"
            disabled={pending !== null}
            onClick={handleClose}
          >
            {t("create.cancel")}
          </Button>
          <Button
            type="submit"
            data-testid="market-edit-submit"
            disabled={pending !== null}
          >
            {pending === "save" ? <LoaderCircle className="animate-spin" /> : null}
            {pending === "save" ? t("edit.saving") : t("edit.submit")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
