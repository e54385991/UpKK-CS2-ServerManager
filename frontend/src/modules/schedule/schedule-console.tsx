"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import {
  createScheduleAction,
  deleteScheduleAction,
  toggleScheduleAction,
} from "@/modules/schedule/actions";
import {
  SCHEDULE_ACTIONS,
  SCHEDULE_TYPES,
  type ScheduleAction,
  type ScheduledTask,
  type ScheduleType,
} from "@/modules/schedule/types";
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
import { Switch } from "@/shared/ui/switch";
import { cn } from "@/shared/lib/cn";

export function ScheduleConsole({
  serverId,
  initial,
}: {
  serverId: number;
  initial: ScheduledTask[];
}) {
  const t = useTranslations("schedule");
  const [tasks, setTasks] = useState(initial);
  const [name, setName] = useState("");
  const [action, setAction] = useState<ScheduleAction>("restart");
  const [scheduleType, setScheduleType] = useState<ScheduleType>("daily");
  const [scheduleValue, setScheduleValue] = useState("03:00");
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  async function create() {
    setPending("create");
    setBanner(null);
    const result = await createScheduleAction(serverId, {
      name,
      action,
      scheduleType,
      scheduleValue,
    });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setTasks((current) => [...current, result.data]);
    setName("");
    setBanner(t("created"));
  }

  async function toggle(taskId: number) {
    setPending(`toggle-${taskId}`);
    setBanner(null);
    setTasks((current) =>
      current.map((item) =>
        item.id === taskId ? { ...item, enabled: !item.enabled } : item,
      ),
    );
    const result = await toggleScheduleAction(serverId, taskId);
    setPending(null);
    if (!result.ok) {
      setTasks((current) =>
        current.map((item) =>
          item.id === taskId ? { ...item, enabled: !item.enabled } : item,
        ),
      );
      setBanner(result.error || t("failed"));
      return;
    }
    setTasks((current) =>
      current.map((item) => (item.id === taskId ? result.data : item)),
    );
  }

  async function remove(taskId: number) {
    setPending(`delete-${taskId}`);
    const result = await deleteScheduleAction(serverId, taskId);
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setTasks((current) => current.filter((item) => item.id !== taskId));
  }

  return (
    <div className="space-y-6">
      {banner ? <p className="text-sm text-fg-muted">{banner}</p> : null}
      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>{t("title")}</CardTitle>
          <CardDescription>{t("help")}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2 sm:col-span-2">
            <Label htmlFor="task-name">{t("name")}</Label>
            <Input id="task-name" value={name} onChange={(event) => setName(event.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="task-action">{t("action")}</Label>
            <Select
              id="task-action"
              value={action}
              onChange={(event) => setAction(event.target.value as ScheduleAction)}
            >
              {SCHEDULE_ACTIONS.map((item) => (
                <option key={item} value={item}>
                  {t(`actions.${item}`)}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="task-type">{t("type")}</Label>
            <Select
              id="task-type"
              value={scheduleType}
              onChange={(event) => setScheduleType(event.target.value as ScheduleType)}
            >
              {SCHEDULE_TYPES.map((item) => (
                <option key={item} value={item}>
                  {t(`types.${item}`)}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-2 sm:col-span-2">
            <Label htmlFor="task-value">{t("value")}</Label>
            <Input
              id="task-value"
              value={scheduleValue}
              onChange={(event) => setScheduleValue(event.target.value)}
            />
          </div>
          <div>
            <Button type="button" disabled={pending === "create" || !name.trim()} onClick={() => void create()}>
              {pending === "create" ? t("creating") : t("create")}
            </Button>
          </div>
        </CardContent>
      </Card>

      {tasks.length === 0 ? (
        <p className="text-sm text-fg-muted">{t("empty")}</p>
      ) : (
        <ul className="space-y-3">
          {tasks.map((task) => (
            <li
              key={task.id}
              className={cn(
                "flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-surface px-4 py-3",
                !task.enabled && "opacity-70",
              )}
            >
              <div className="space-y-1">
                <p className="font-medium">{task.name}</p>
                <p className="text-xs text-fg-muted">
                  {t(`actions.${task.action}`)} · {t(`types.${task.scheduleType}`)} · {task.scheduleValue}
                </p>
                {task.nextRun ? (
                  <p className="text-xs text-fg-subtle">
                    {t("next")}: {task.nextRun}
                  </p>
                ) : null}
              </div>
              <div className="flex items-center gap-2">
                <Switch
                  id={`schedule-enabled-${task.id}`}
                  checked={task.enabled}
                  disabled={pending === `toggle-${task.id}`}
                  label={t("enabled")}
                  onCheckedChange={() => {
                    void toggle(task.id);
                  }}
                />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={pending === `delete-${task.id}`}
                  onClick={() => void remove(task.id)}
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
