"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Save, TriangleAlert } from "lucide-react";
import { updateServerAction } from "@/modules/servers/actions";
import type { ServerDetail } from "@/modules/servers/api";
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

export function ServerMonitoringForm({ server }: { server: ServerDetail }) {
  const t = useTranslations("serverMonitoring");
  const router = useRouter();
  const [enablePanel, setEnablePanel] = useState(server.enablePanelMonitoring);
  const [autoRestart, setAutoRestart] = useState(server.autoRestartOnCrash);
  const [enableA2s, setEnableA2s] = useState(server.enableA2sMonitoring);
  const [autoUpdate, setAutoUpdate] = useState(server.enableAutoUpdate);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    setNotice(null);
    const result = await updateServerAction(server.id, {
      enablePanelMonitoring: enablePanel,
      monitorIntervalSeconds: Number(form.get("interval")),
      autoRestartOnCrash: autoRestart,
      enableA2sMonitoring: enableA2s,
      a2sFailureThreshold: Number(form.get("a2sThreshold")),
      a2sCheckIntervalSeconds: Number(form.get("a2sInterval")),
      enableAutoUpdate: autoUpdate,
    });
    setPending(false);
    if (!result.ok) {
      setError(result.error || t("failed"));
      return;
    }
    setNotice(t("saved"));
    router.refresh();
  }

  return (
    <form onSubmit={(event) => void onSubmit(event)} className="max-w-2xl space-y-6">
      {server.isSshDown ? (
        <Card className="border-warn/30 bg-warn-muted/30 px-5 py-4 text-sm text-warn">
          <p>{t("sshDown")}</p>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("title")}</CardTitle>
            <CardDescription>{t("help")}</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <p className="text-sm text-fg-muted">
            {t("lastSsh")}:{" "}
            {server.lastSshSuccess
              ? new Date(server.lastSshSuccess).toLocaleString()
              : t("neverSsh")}
          </p>

          <SwitchRow
            id="enablePanel"
            label={t("fields.enablePanel")}
            checked={enablePanel}
            onCheckedChange={setEnablePanel}
          />
          <Field label={t("fields.interval")} htmlFor="interval">
            <Input
              id="interval"
              name="interval"
              type="number"
              min={10}
              max={3600}
              required
              defaultValue={server.monitorIntervalSeconds}
            />
          </Field>
          <SwitchRow
            id="autoRestart"
            label={t("fields.autoRestart")}
            checked={autoRestart}
            onCheckedChange={setAutoRestart}
          />
          <SwitchRow
            id="enableA2s"
            label={t("fields.enableA2s")}
            checked={enableA2s}
            onCheckedChange={setEnableA2s}
          />
          <Field label={t("fields.a2sThreshold")} htmlFor="a2sThreshold">
            <Input
              id="a2sThreshold"
              name="a2sThreshold"
              type="number"
              min={1}
              max={10}
              required
              defaultValue={server.a2sFailureThreshold}
            />
          </Field>
          <Field label={t("fields.a2sInterval")} htmlFor="a2sInterval">
            <Input
              id="a2sInterval"
              name="a2sInterval"
              type="number"
              min={15}
              max={3600}
              required
              defaultValue={server.a2sCheckIntervalSeconds}
            />
          </Field>
          <SwitchRow
            id="autoUpdate"
            label={t("fields.autoUpdate")}
            checked={autoUpdate}
            onCheckedChange={setAutoUpdate}
          />

          {error ? (
            <p className="flex items-center gap-2 text-sm text-danger">
              <TriangleAlert className="size-4" />
              {error}
            </p>
          ) : null}
          {notice ? <p className="text-sm text-ok">{notice}</p> : null}

          <Button type="submit" disabled={pending}>
            <Save />
            {pending ? t("saving") : t("save")}
          </Button>
        </CardContent>
      </Card>
    </form>
  );
}

function SwitchRow({
  id,
  label,
  checked,
  onCheckedChange,
}: {
  id: string;
  label: string;
  checked: boolean;
  onCheckedChange: (next: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <Label htmlFor={id}>{label}</Label>
      <Switch id={id} label={label} checked={checked} onCheckedChange={onCheckedChange} />
    </div>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  );
}
