"use client";

import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { LoaderCircle, WandSparkles } from "lucide-react";
import {
  createMarketPluginAction,
  fetchMarketRepoInfoAction,
  listPluginDependencyOptionsAction,
} from "@/modules/plugins/actions";
import {
  PLUGIN_CATEGORIES,
  isPluginCategory,
  type GitHubRepoInfo,
  type MarketPluginCreateInput,
  type PluginCategory,
  type PluginDependencyOptions,
} from "@/modules/plugins/types";
import { notify } from "@/shared/feedback/notify";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Input, Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";
import { Textarea } from "@/shared/ui/textarea";

type PendingAction = "repo-info" | "dependencies" | "create" | null;

export function MarketPluginCreateDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const t = useTranslations("plugins");
  const router = useRouter();
  const [githubUrl, setGithubUrl] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [author, setAuthor] = useState("");
  const [version, setVersion] = useState("");
  const [category, setCategory] = useState<PluginCategory>("other");
  const [iconUrl, setIconUrl] = useState("");
  const [tags, setTags] = useState("");
  const [customInstallPath, setCustomInstallPath] = useState("");
  const [isRecommended, setIsRecommended] = useState(false);
  const [dependencySearch, setDependencySearch] = useState("");
  const [dependencyOptions, setDependencyOptions] = useState<
    readonly PluginDependencyOptions[]
  >([]);
  const [dependencyIds, setDependencyIds] = useState<readonly number[]>([]);
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
        setDependencyOptions(result.data);
      });
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [dependencySearch, open, t]);

  function resetForm() {
    setGithubUrl("");
    setTitle("");
    setDescription("");
    setAuthor("");
    setVersion("");
    setCategory("other");
    setIconUrl("");
    setTags("");
    setCustomInstallPath("");
    setIsRecommended(false);
    setDependencySearch("");
    setDependencyOptions([]);
    setDependencyIds([]);
    setPending(null);
    setError(null);
    setNotice(null);
  }

  function handleClose() {
    if (pending) return;
    onClose();
  }

  async function autoFill() {
    const value = githubUrl.trim();
    if (!value) {
      setError(t("create.invalidGithubUrl"));
      setNotice(null);
      return;
    }
    setPending("repo-info");
    setError(null);
    setNotice(null);
    const result = await fetchMarketRepoInfoAction(value);
    setPending(null);
    if (!result.ok) {
      setError(result.error || t("create.autoFillFailed"));
      return;
    }
    if (!result.data.success) {
      setError(result.data.error || t("create.autoFillFailed"));
      return;
    }
    if (result.data.repoName) {
      setTitle((current) => current.trim() || result.data.repoName || "");
    }
    // Prefer the full README over GitHub's one-line description: the console
    // renders the description as Markdown, so the long form is the useful one.
    const body = result.data.readme || result.data.description;
    if (body) {
      setDescription((current) => current.trim() || body);
    }
    if (result.data.author) {
      setAuthor((current) => current.trim() || result.data.author || "");
    }
    setNotice(
      result.data.readme
        ? t("create.autoFillReadmeSuccess")
        : t("create.autoFillSuccess"),
    );
  }

  function selectedDependencies(event: ChangeEvent<HTMLSelectElement>) {
    setDependencyIds(
      Array.from(event.target.selectedOptions, (option) => Number(option.value)),
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const input: MarketPluginCreateInput = {
      githubUrl: githubUrl.trim(),
      title: title.trim(),
      description: description.trim() || null,
      author: author.trim() || null,
      version: version.trim() || null,
      category,
      iconUrl: iconUrl.trim() || null,
      tags: tags.trim() || null,
      customInstallPath: customInstallPath.trim() || null,
      isRecommended,
      dependencyIds,
    };
    if (!input.githubUrl || !input.title || !input.category) {
      setError(t("create.requiredFields"));
      setNotice(null);
      return;
    }
    setPending("create");
    setError(null);
    setNotice(null);
    const result = await createMarketPluginAction(input);
    setPending(null);
    if (!result.ok) {
      setError(result.error || t("create.failed"));
      return;
    }
    notify.success(t("create.success", { name: result.data.title }));
    resetForm();
    onClose();
    router.refresh();
  }

  return (
    <Dialog
      open={open}
      title={t("create.title")}
      description={t("create.description")}
      closeLabel={t("create.cancel")}
      onClose={handleClose}
      className="max-w-4xl"
    >
      <form
        className="space-y-5"
        data-testid="market-create-form"
        onSubmit={(event) => void submit(event)}
      >
        <div className="space-y-1.5">
          <Label htmlFor="market-create-github-url">
            {t("create.githubUrl")} *
          </Label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              id="market-create-github-url"
              type="url"
              value={githubUrl}
              required
              placeholder={t("create.githubPlaceholder")}
              onChange={(event) => setGithubUrl(event.target.value)}
            />
            <Button
              type="button"
              variant="secondary"
              className="shrink-0"
              data-testid="market-create-autofill"
              disabled={pending !== null}
              onClick={() => void autoFill()}
            >
              {pending === "repo-info" ? (
                <LoaderCircle className="animate-spin" />
              ) : (
                <WandSparkles />
              )}
              {pending === "repo-info"
                ? t("create.autoFilling")
                : t("create.autoFill")}
            </Button>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="market-create-title">{t("create.titleField")} *</Label>
            <Input
              id="market-create-title"
              value={title}
              required
              maxLength={255}
              onChange={(event) => setTitle(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="market-create-author">{t("create.author")}</Label>
            <Input
              id="market-create-author"
              value={author}
              maxLength={255}
              onChange={(event) => setAuthor(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="market-create-version">{t("create.version")}</Label>
            <Input
              id="market-create-version"
              value={version}
              maxLength={50}
              onChange={(event) => setVersion(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="market-create-category">
              {t("create.category")} *
            </Label>
            <Select
              id="market-create-category"
              value={category}
              required
              onChange={(event) => {
                if (isPluginCategory(event.target.value)) {
                  setCategory(event.target.value);
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
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="market-create-description">
            {t("create.descriptionField")}
          </Label>
          <Textarea
            id="market-create-description"
            value={description}
            rows={6}
            maxLength={10000}
            aria-describedby="market-create-description-hint"
            onChange={(event) => setDescription(event.target.value)}
          />
          <p id="market-create-description-hint" className="text-xs text-fg-subtle">
            {t("create.descriptionMarkdownHint")}
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="market-create-icon">{t("create.iconUrl")}</Label>
            <Input
              id="market-create-icon"
              type="url"
              value={iconUrl}
              maxLength={500}
              onChange={(event) => setIconUrl(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="market-create-tags">{t("create.tags")}</Label>
            <Input
              id="market-create-tags"
              value={tags}
              onChange={(event) => setTags(event.target.value)}
            />
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="market-create-install-path">
            {t("create.customInstallPath")}
          </Label>
          <Input
            id="market-create-install-path"
            value={customInstallPath}
            maxLength={255}
            placeholder={t("create.customInstallPathPlaceholder")}
            onChange={(event) => setCustomInstallPath(event.target.value)}
          />
          <p className="text-xs text-fg-subtle">{t("create.customInstallPathHint")}</p>
        </div>

        <div className="space-y-2 rounded-lg border border-line bg-surface-overlay p-3">
          <Label htmlFor="market-create-dependency-search">
            {t("create.dependencies")}
          </Label>
          <Input
            id="market-create-dependency-search"
            value={dependencySearch}
            placeholder={t("create.dependencySearch")}
            onChange={(event) => setDependencySearch(event.target.value)}
          />
          <Select
            multiple
            size={8}
            value={dependencyIds.map(String)}
            aria-label={t("create.dependencies")}
            data-testid="market-create-dependencies"
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
          {pending === "dependencies" ? (
            <p className="flex items-center gap-2 text-xs text-fg-subtle" role="status">
              <LoaderCircle className="size-3 animate-spin" />
              {t("create.dependencyLoading")}
            </p>
          ) : null}
          {pending !== "dependencies" && dependencyOptions.length === 0 ? (
            <p className="text-xs text-fg-subtle">{t("create.dependencyEmpty")}</p>
          ) : null}
        </div>

        <label className="flex items-start gap-2 text-sm text-fg-muted">
          <input
            type="checkbox"
            checked={isRecommended}
            className="mt-0.5 size-4 rounded border-line accent-primary"
            onChange={(event) => setIsRecommended(event.target.checked)}
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
            data-testid="market-create-submit"
            disabled={pending !== null}
          >
            {pending === "create" ? (
              <LoaderCircle className="animate-spin" />
            ) : null}
            {pending === "create" ? t("create.creating") : t("create.submit")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
