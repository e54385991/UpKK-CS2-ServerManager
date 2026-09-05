"use client";

import { useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Check, Clipboard, RefreshCw, Save, Wrench } from "lucide-react";
import {
  probeServerCompatibilityAction,
  saveExecstackPolicyAction,
} from "@/modules/servers/additional-fixes-actions";
import type { ServerDetail } from "@/modules/servers/api";
import { copyText } from "@/shared/lib/clipboard";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/shared/ui/card";

const DEFAULT_TARGET = "counterstrikesharp/bin/linuxsteamrt64/counterstrikesharp.so";

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

export function AdditionalFixesConsole({ initial }: { initial: ServerDetail }) {
  const t = useTranslations("serverAdditionalFixes");
  const [detectedOsId, setDetectedOsId] = useState(initial.osId);
  const [detectedOsVersion, setDetectedOsVersion] = useState(initial.osVersion);
  const [effective, setEffective] = useState(initial.clearExecstackEffective);
  const [override, setOverride] = useState<boolean | null>(initial.clearExecstackOverride);
  const [restart, setRestart] = useState(initial.execstackFixOnRestart);
  const [framework, setFramework] = useState(initial.execstackFixOnFramework);
  const [gameUpdate, setGameUpdate] = useState(initial.execstackFixOnGameUpdate);
  const [targets, setTargets] = useState(initial.execstackFixTargets.join("\n"));
  const [saving, setSaving] = useState(false);
  const [probing, setProbing] = useState(false);
  const [copied, setCopied] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const commandRef = useRef<HTMLPreElement>(null);

  const targetList = useMemo(
    () => targets.split("\n").map((value) => value.trim()).filter(Boolean),
    [targets],
  );
  const command = useMemo(() => {
    const selected = targetList.length > 0 ? targetList : [DEFAULT_TARGET];
    const base = `${initial.gameDirectory}/cs2/game/csgo/addons`;
    return selected.map((target) => `patchelf --clear-execstack ${shellQuote(`${base}/${target}`)}`).join("\n");
  }, [initial.gameDirectory, targetList]);

  async function save(next: Partial<{
    override: boolean | null;
    restart: boolean;
    framework: boolean;
    gameUpdate: boolean;
    targets: string[];
  }> = {}) {
    const values = {
      override: next.override === undefined ? override : next.override,
      restart: next.restart === undefined ? restart : next.restart,
      framework: next.framework === undefined ? framework : next.framework,
      gameUpdate: next.gameUpdate === undefined ? gameUpdate : next.gameUpdate,
      targets: next.targets === undefined ? targetList : next.targets,
    };
    setSaving(true);
    setError(null);
    setMessage(null);
    const result = await saveExecstackPolicyAction(initial.id, {
      clearExecstackOverride: values.override,
      execstackFixOnRestart: values.restart,
      execstackFixOnFramework: values.framework,
      execstackFixOnGameUpdate: values.gameUpdate,
      execstackFixTargets: values.targets,
    });
    setSaving(false);
    if (!result.ok) {
      setOverride(override);
      setRestart(restart);
      setFramework(framework);
      setGameUpdate(gameUpdate);
      setTargets(targets);
      setEffective(effective);
      setError(result.error);
      return;
    }
    setEffective(result.data.clearExecstackEffective);
    setMessage(t("saved"));
  }

  async function probe() {
    setProbing(true);
    setError(null);
    const result = await probeServerCompatibilityAction(initial.id);
    setProbing(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setEffective(result.data.clearExecstackEffective);
    setDetectedOsId(result.data.osId);
    setDetectedOsVersion(result.data.osVersion);
    setMessage(t("probed"));
  }

  async function copyCommand() {
    const ok = await copyText(command);
    setCopied(ok);
    if (ok) window.setTimeout(() => setCopied(false), 1800);
    else {
      setError(t("copyFailed"));
      if (commandRef.current) {
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(commandRef.current);
        selection?.removeAllRanges();
        selection?.addRange(range);
      }
    }
  }

  return (
    <div className="max-w-4xl space-y-6">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Wrench className="size-5 text-primary" />
            <CardTitle>{t("title")}</CardTitle>
          </div>
          <CardDescription>{t("description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="grid gap-3 rounded-md border border-line bg-surface-raised/40 p-4 text-sm sm:grid-cols-3">
            <div><p className="text-fg-muted">{t("system")}</p><p className="font-medium text-fg">{detectedOsId && detectedOsVersion ? `${detectedOsId} ${detectedOsVersion}` : t("unknown")}</p></div>
            <div><p className="text-fg-muted">{t("source")}</p><p className="font-medium text-fg">{override === null ? t("automatic") : t("manual")}</p></div>
            <div><p className="text-fg-muted">{t("effective")}</p><Badge tone={effective ? "ok" : "neutral"}>{effective ? t("enabled") : t("disabled")}</Badge></div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" size="sm" onClick={() => void probe()} disabled={probing}>
              <RefreshCw className={probing ? "animate-spin" : undefined} />{t("probe")}
            </Button>
            {override !== null ? <Button type="button" variant="ghost" size="sm" onClick={() => { setOverride(null); setEffective(initial.clearExecstackEffective); void save({ override: null }); }}>{t("restoreAutomatic")}</Button> : null}
          </div>
          <label className="flex items-start gap-3 text-sm">
            <input type="checkbox" className="mt-1 size-4 accent-primary" checked={override ?? effective} onChange={(event) => { const value = event.target.checked; setOverride(value); setEffective(value); void save({ override: value }); }} />
            <span><span className="font-medium text-fg">{t("enabledLabel")}</span><span className="mt-1 block text-xs text-fg-muted">{t("enabledHelp")}</span></span>
          </label>
          <div className="grid gap-3 sm:grid-cols-3">
            <Toggle label={t("restartTrigger")} checked={restart} onChange={(value) => { setRestart(value); void save({ restart: value }); }} />
            <Toggle label={t("frameworkTrigger")} checked={framework} onChange={(value) => { setFramework(value); void save({ framework: value }); }} />
            <Toggle label={t("gameUpdateTrigger")} checked={gameUpdate} onChange={(value) => { setGameUpdate(value); void save({ gameUpdate: value }); }} />
          </div>
          <label className="block space-y-2 text-sm">
            <span className="font-medium text-fg">{t("targets")}</span>
            <span className="block text-xs text-fg-muted">{t("targetsHelp")}</span>
            <textarea value={targets} onChange={(event) => setTargets(event.target.value)} onBlur={() => void save({ targets: targetList })} rows={4} className="w-full rounded-md border border-line bg-surface px-3 py-2 font-mono text-xs text-fg" spellCheck={false} />
          </label>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="button" onClick={() => void save()} disabled={saving || targetList.length === 0}><Save />{saving ? t("saving") : t("save")}</Button>
            {message ? <span className="text-sm text-success">{message}</span> : null}
            {error ? <span className="text-sm text-danger">{error}</span> : null}
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>{t("commandTitle")}</CardTitle><CardDescription>{t("commandHelp")}</CardDescription></CardHeader>
        <CardContent className="space-y-3">
          <div className="relative rounded-md border border-line bg-surface-raised px-3 py-2.5"><pre ref={commandRef} className="overflow-x-auto pr-20 whitespace-pre-wrap font-mono text-xs text-fg">{command}</pre><Button type="button" size="sm" variant="ghost" className="absolute right-1.5 top-1.5" onClick={() => void copyCommand()}>{copied ? <Check /> : <Clipboard />}{copied ? t("copied") : t("copy")}</Button></div>
          <p className="text-xs text-fg-subtle">{t("commandNote")}</p>
        </CardContent>
      </Card>
    </div>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <label className="flex items-center gap-2 rounded-md border border-line px-3 py-2 text-sm text-fg"><input type="checkbox" className="size-4 accent-primary" checked={checked} onChange={(event) => onChange(event.target.checked)} />{label}</label>;
}
