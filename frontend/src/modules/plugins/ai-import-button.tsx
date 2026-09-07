"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Sparkles } from "lucide-react";
import type { components } from "@/shared/api/schema";
import { aiImportReadiness, submitAIImport } from "@/modules/plugins/ai-import-actions";
import { trackAIImport } from "@/modules/plugins/ai-import-activity";
import { randomId } from "@/shared/lib/random-id";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Input, Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";
import { Textarea } from "@/shared/ui/textarea";

type Options = components["schemas"]["ImportOptions"];
type Readiness = components["schemas"]["PluginAIReadinessView"];
const defaults: Options = { framework: "all", keywords: "", min_stars: 10, min_forks: 0, sort: "stars", updated_within_days: 90, minutes: 15, max_plugins: 20, repositories: [] };

export function AIImportButton() {
  const t = useTranslations("plugins.aiImport");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState<Readiness | null>(null);
  const [options, setOptions] = useState(defaults);
  const [repositories, setRepositories] = useState("");
  const [ack, setAck] = useState(false);
  const [error, setError] = useState("");
  const requestId = useRef<string | null>(null);
  useEffect(() => {
    if (!open) return;
    let active = true;
    void aiImportReadiness().then(result => {
      if (!active) return;
      if (result.ok) setReady(result.data);
      else setError(t("requestFailed"));
    }).catch(() => {
      if (active) setError(t("requestFailed"));
    });
    return () => { active = false; };
  }, [open, t]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const result = await submitAIImport({ request_id: requestId.current ??= randomId(), options: { ...options, repositories: repositories.split(/\s+/).filter(Boolean) }, acknowledge_ai_warning: ack });
      if (!result.ok) { setError(result.error || t("requestFailed")); return; }
      setOpen(false);
      trackAIImport(result.data);
    } catch {
      setError(t("requestFailed"));
    } finally { setBusy(false); }
  }
  return <>
    <Button variant="outline" onClick={() => { setReady(null); setError(""); setAck(false); requestId.current = null; setOpen(true); }}><Sparkles />{t("open")}</Button>
    <Dialog open={open} closeLabel={t("cancel")} onClose={() => { if (!busy) setOpen(false); }} title={t("open")}>
      <form onSubmit={submit} className="space-y-4">
        <p className="text-sm text-fg-muted">{t("description")}</p>
        <div className="rounded-md border border-line p-3 text-sm">
          <p>{t("model")}: {ready?.ai_model ?? "—"}</p>
          <p>GitHub: {ready?.token_valid ? ready.token_account : t("tokenRequired")}</p>
          <Link href="/settings" className="text-primary underline">{t("settings")}</Link>
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div><Label htmlFor="ai-framework">{t("framework")}</Label><Select id="ai-framework" value={options.framework} onChange={e => setOptions({ ...options, framework: e.target.value as Options["framework"] })}>
            <option value="all">{t("allFrameworks")}</option><option value="counterstrikesharp">CounterStrikeSharp</option><option value="swiftly">SwiftlyS2</option>
          </Select></div>
          <div><Label htmlFor="ai-sort">{t("sort")}</Label><Select id="ai-sort" value={options.sort} onChange={e => setOptions({ ...options, sort: e.target.value as Options["sort"] })}><option value="stars">Star</option><option value="forks">Fork</option><option value="updated">{t("updated")}</option></Select></div>
          {([['min_stars', 'stars', 0, 1000000], ['min_forks', 'forks', 0, 1000000], ['updated_within_days', 'days', 1, 3650], ['minutes', 'minutes', 1, 120], ['max_plugins', 'count', 1, 100]] as const).map(([key,label,min,max]) => <div key={key}><Label htmlFor={`ai-${key}`}>{t(label)}</Label><Input id={`ai-${key}`} required type="number" min={min} max={max} value={options[key]} onChange={e => setOptions({ ...options, [key]: Number(e.target.value) })} /></div>)}
          <div><Label htmlFor="ai-keywords">{t("keywords")}</Label><Input id="ai-keywords" maxLength={200} value={options.keywords} onChange={e => setOptions({ ...options, keywords: e.target.value })} /></div>
        </div>
        <div><Label htmlFor="ai-repositories">{t("repositories")}</Label><Textarea id="ai-repositories" value={repositories} onChange={e => setRepositories(e.target.value)} placeholder="https://github.com/samyycX/CS2-PlayerModelChanger&#10;https://github.com/K4ryuu/K4-Missions-SwiftlyS2" /></div>
        <p className="rounded-md border border-warn/30 bg-warn-muted p-3 text-sm text-warn">{t("warning")}</p>
        <label className="flex gap-2 text-sm"><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} />{t("acknowledge")}</label>
        {error && <p role="alert" className="text-sm text-danger">{error}</p>}
        <Button type="submit" disabled={busy || !ack || !ready?.token_valid || !ready.ai_configured}>{busy ? t("submitting") : t("submit")}</Button>
      </form>
    </Dialog>
  </>;
}
