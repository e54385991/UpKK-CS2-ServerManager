"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import {
  deleteCommandAction,
  executeOnceCommandAction,
  executeSavedCommandAction,
  saveCommandAction,
} from "@/modules/commands/actions";
import {
  COMMAND_TARGETS,
  type CommandExecuteResult,
  type CommandTarget,
  type CustomCommand,
} from "@/modules/commands/types";
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
import { Select } from "@/shared/ui/select";
import { Textarea } from "@/shared/ui/textarea";

export function CommandsConsole({
  serverId,
  initial,
}: {
  serverId: number;
  initial: CustomCommand[];
}) {
  const t = useTranslations("quickCommands");
  const [commands, setCommands] = useState(initial);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [target, setTarget] = useState<CommandTarget>("host");
  const [body, setBody] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [result, setResult] = useState<CommandExecuteResult | null>(null);

  const lines = body
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean).length;

  function resetForm() {
    setEditingId(null);
    setName("");
    setTarget("host");
    setBody("");
  }

  function load(command: CustomCommand) {
    setEditingId(command.id);
    setName(command.name);
    setTarget(command.target);
    setBody(command.commands);
  }

  async function save() {
    setPending("save");
    setBanner(null);
    const next = await saveCommandAction(serverId, {
      id: editingId ?? undefined,
      name,
      target,
      commands: body,
    });
    setPending(null);
    if (!next.ok) {
      setBanner(next.error || t("failed"));
      return;
    }
    setCommands((current) => {
      if (editingId == null) return [...current, next.data];
      return current.map((item) => (item.id === next.data.id ? next.data : item));
    });
    resetForm();
    setBanner(t("saved"));
  }

  async function remove(commandId: number) {
    if (!(await confirm(t("confirmDelete")))) return;
    setPending(`delete-${commandId}`);
    const next = await deleteCommandAction(serverId, commandId);
    setPending(null);
    if (!next.ok) {
      setBanner(next.error || t("failed"));
      return;
    }
    setCommands((current) => current.filter((item) => item.id !== commandId));
    if (editingId === commandId) resetForm();
    setBanner(t("deleted"));
  }

  async function runSaved(command: CustomCommand) {
    setPending(`run-${command.id}`);
    setResult(null);
    const next = await executeSavedCommandAction(serverId, command.id);
    setPending(null);
    if (!next.ok) {
      setBanner(next.error || t("failed"));
      return;
    }
    setResult(next.data);
    setBanner(next.data.message);
  }

  async function runOnce() {
    setPending("once");
    setResult(null);
    const next = await executeOnceCommandAction(serverId, {
      target,
      commands: body,
    });
    setPending(null);
    if (!next.ok) {
      setBanner(next.error || t("failed"));
      return;
    }
    setResult(next.data);
    setBanner(next.data.message);
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>{t("title")}</CardTitle>
          <CardDescription>{t("help")}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          {banner ? (
            <p className="text-sm text-fg-muted sm:col-span-2" role="status">
              {banner}
            </p>
          ) : null}
          <div className="space-y-2">
            <Label htmlFor="command-name">{t("name")}</Label>
            <Input
              id="command-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={t("namePlaceholder")}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="command-target">{t("target")}</Label>
            <Select
              id="command-target"
              value={target}
              onChange={(event) => setTarget(event.target.value as CommandTarget)}
            >
              {COMMAND_TARGETS.map((item) => (
                <option key={item} value={item}>
                  {t(`targets.${item}`)}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-2 sm:col-span-2">
            <Label htmlFor="command-body">{t("commands")}</Label>
            <Textarea
              id="command-body"
              value={body}
              onChange={(event) => setBody(event.target.value)}
              placeholder={t("commandsPlaceholder")}
            />
            <p className="text-xs text-fg-subtle">
              {t("lineCount")}: {lines}
            </p>
          </div>
          <div className="flex flex-wrap gap-2 sm:col-span-2">
            <Button
              type="button"
              disabled={Boolean(pending) || lines === 0}
              onClick={() => void runOnce()}
            >
              {pending === "once" ? t("sending") : t("sendOnce")}
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={Boolean(pending) || !name.trim() || lines === 0}
              onClick={() => void save()}
            >
              {pending === "save"
                ? t("saving")
                : editingId != null
                  ? t("update")
                  : t("save")}
            </Button>
            {editingId != null ? (
              <Button type="button" variant="outline" onClick={resetForm}>
                {t("cancel")}
              </Button>
            ) : null}
          </div>
          {result ? (
            <pre className="max-h-48 overflow-auto rounded-md border border-line bg-surface-raised p-3 font-mono text-xs text-fg sm:col-span-2">
              {result.log || result.message}
            </pre>
          ) : null}
        </CardContent>
      </Card>

      {commands.length === 0 ? (
        <p className="text-sm text-fg-muted">{t("empty")}</p>
      ) : (
        <ul className="space-y-3">
          {commands.map((command) => (
            <li
              key={command.id}
              className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-line bg-surface px-4 py-3"
            >
              <div className="min-w-0 space-y-1">
                <p className="font-medium">{command.name}</p>
                <Badge>{t(`targets.${command.target}`)}</Badge>
                <pre className="max-h-20 overflow-auto text-xs text-fg-muted">
                  {command.commands}
                </pre>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  disabled={Boolean(pending)}
                  onClick={() => void runSaved(command)}
                >
                  {pending === `run-${command.id}` ? t("sending") : t("run")}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => load(command)}
                >
                  {t("edit")}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={pending === `delete-${command.id}`}
                  onClick={() => void remove(command.id)}
                >
                  {t("delete")}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
