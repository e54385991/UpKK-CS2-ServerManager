"use client";

import { useState, type FormEvent } from "react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { FilePlus2, Pencil, Send, Trash2 } from "lucide-react";
import { confirm } from "@/shared/feedback";
import {
  deleteAnnouncementAction,
  refreshAdminAnnouncementsAction,
  saveAnnouncementAction,
} from "@/modules/announcements/actions";
import type { Announcement, AnnouncementWrite } from "@/modules/announcements/types";
import { Button } from "@/shared/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/ui/card";
import { Input, Label } from "@/shared/ui/input";
import { Textarea } from "@/shared/ui/textarea";

const Markdown = dynamic(
  () => import("@/shared/ui/markdown").then((module) => module.Markdown),
  { loading: () => null },
);

const EMPTY_FORM: AnnouncementWrite = {
  title: "",
  bodyMarkdown: "",
  isPublished: false,
};

export function AnnouncementManager({
  initial,
  loadError,
}: {
  initial: readonly Announcement[];
  loadError?: string;
}) {
  const t = useTranslations("settings");
  const router = useRouter();
  const [items, setItems] = useState([...initial]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [form, setForm] = useState<AnnouncementWrite>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(loadError ?? "");

  function select(item: Announcement) {
    setSelectedId(item.id);
    setForm({
      title: item.title,
      bodyMarkdown: item.bodyMarkdown,
      isPublished: item.isPublished,
    });
    setMessage("");
  }

  function startNew() {
    setSelectedId(null);
    setForm(EMPTY_FORM);
    setMessage("");
  }

  async function reload() {
    const result = await refreshAdminAnnouncementsAction();
    if (result.ok) {
      setItems(result.data);
      setMessage("");
    } else {
      setMessage(result.error || t("announcements.loadFailed"));
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const result = await saveAnnouncementAction(selectedId, form);
      if (!result.ok) {
        setMessage(result.error || t("announcements.saveFailed"));
        return;
      }
      await reload();
      setSelectedId(result.data.id);
      setForm({
        title: result.data.title,
        bodyMarkdown: result.data.bodyMarkdown,
        isPublished: result.data.isPublished,
      });
      setMessage(t("announcements.saved"));
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  async function remove(item: Announcement) {
    if (!(await confirm(t("announcements.deleteConfirm", { title: item.title })))) return;
    setBusy(true);
    setMessage("");
    try {
      const result = await deleteAnnouncementAction(item.id);
      if (!result.ok) {
        setMessage(result.error || t("announcements.deleteFailed"));
        return;
      }
      setItems((current) => current.filter((entry) => entry.id !== item.id));
      if (selectedId === item.id) startNew();
      setMessage(t("announcements.deleted"));
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(20rem,0.85fr)]">
      <Card>
        <CardHeader>
          <div className="min-w-0">
            <CardTitle>{t("announcements.listTitle")}</CardTitle>
            <p className="mt-1 text-sm text-fg-muted">{t("announcements.listHelp")}</p>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={startNew}>
            <FilePlus2 />
            {t("announcements.new")}
          </Button>
        </CardHeader>
        <CardContent className="space-y-2">
          {items.length === 0 ? (
            <p className="py-5 text-sm text-fg-muted">{t("announcements.empty")}</p>
          ) : (
            items.map((item) => (
              <article
                key={item.id}
                className="flex items-center gap-3 rounded-md border border-line px-3 py-3"
              >
                <button
                  type="button"
                  onClick={() => select(item)}
                  className="min-w-0 flex-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
                >
                  <span className="block truncate text-sm font-medium text-fg">{item.title}</span>
                  <span className="mt-1 block text-xs text-fg-subtle">
                    {item.isPublished
                      ? t("announcements.published")
                      : t("announcements.draft")}
                  </span>
                </button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={t("announcements.edit", { title: item.title })}
                  onClick={() => select(item)}
                >
                  <Pencil />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={t("announcements.delete", { title: item.title })}
                  disabled={busy}
                  onClick={() => void remove(item)}
                >
                  <Trash2 className="text-danger" />
                </Button>
              </article>
            ))
          )}
          {items.length >= 200 ? (
            <p className="text-xs text-fg-subtle">{t("announcements.listLimit")}</p>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>
            {selectedId === null ? t("announcements.createTitle") : t("announcements.editTitle")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={(event) => void submit(event)}>
            <div>
              <Label htmlFor="announcement-title" required>
                {t("announcements.titleLabel")}
              </Label>
              <Input
                id="announcement-title"
                required
                maxLength={160}
                value={form.title}
                onChange={(event) => setForm((current) => ({ ...current, title: event.target.value }))}
              />
            </div>
            <div>
              <Label htmlFor="announcement-markdown" required>
                {t("announcements.contentLabel")}
              </Label>
              <Textarea
                id="announcement-markdown"
                required
                maxLength={20000}
                rows={12}
                value={form.bodyMarkdown}
                onChange={(event) => setForm((current) => ({ ...current, bodyMarkdown: event.target.value }))}
                placeholder={t("announcements.markdownPlaceholder")}
                className="min-h-64 resize-y font-mono"
              />
              <p className="mt-1 text-xs text-fg-subtle">{t("announcements.markdownHelp")}</p>
            </div>
            <label className="flex items-center gap-2 text-sm text-fg">
              <input
                type="checkbox"
                checked={form.isPublished}
                onChange={(event) => setForm((current) => ({ ...current, isPublished: event.target.checked }))}
                className="size-4 accent-primary"
              />
              {t("announcements.publishNow")}
            </label>
            {form.bodyMarkdown.trim() ? (
              <div className="rounded-md border border-line bg-surface-raised p-3">
                <p className="mb-2 text-xs font-medium text-fg-subtle">{t("announcements.preview")}</p>
                <Markdown source={form.bodyMarkdown} />
              </div>
            ) : null}
            {message ? (
              <p role="status" className="text-sm text-fg-muted">{message}</p>
            ) : null}
            <div className="flex flex-wrap gap-2">
              <Button type="submit" disabled={busy}>
                <Send />
                {busy ? t("announcements.saving") : t("announcements.save")}
              </Button>
              {selectedId !== null ? (
                <Button type="button" variant="outline" onClick={startNew}>
                  {t("announcements.cancelEdit")}
                </Button>
              ) : null}
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
