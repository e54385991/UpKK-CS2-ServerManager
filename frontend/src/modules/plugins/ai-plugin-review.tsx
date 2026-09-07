"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { components } from "@/shared/api/schema";
import { reviewAIPlugin } from "@/modules/plugins/ai-import-actions";
import { Button } from "@/shared/ui/button";
import { Input, Label } from "@/shared/ui/input";
import { Textarea } from "@/shared/ui/textarea";

type Info = components["schemas"]["PluginAIInfo"];
export function AIPluginReview({ pluginId, initial, canEdit }: { pluginId: number; initial: Info; canEdit: boolean }) {
  const t = useTranslations("plugins.aiImport");
  const router = useRouter();
  const [info, setInfo] = useState(initial);
  const [asset, setAsset] = useState(initial.installation?.asset_glob ?? "*");
  const [source, setSource] = useState(initial.installation?.source_prefix ?? "");
  const [target, setTarget] = useState(initial.installation?.target_path ?? "");
  const [requirements, setRequirements] = useState((initial.requirements ?? []).join("\n"));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return <section className="space-y-3 rounded-md border border-warn/30 p-4">
    <h3 className="font-semibold">{info.reviewed ? t("reviewed") : t("needsReview")}</h3>
    <p className="text-sm text-warn">{t("warning")}</p>
    <p className="text-xs">{t("model")}: {info.model}</p>
    {canEdit ? <>
      <div><Label htmlFor="ai-rule-asset">{t("assetGlob")}</Label><Input id="ai-rule-asset" value={asset} onChange={e => setAsset(e.target.value)} /></div>
      <div><Label htmlFor="ai-rule-source">{t("source")}</Label><Input id="ai-rule-source" value={source} onChange={e => setSource(e.target.value)} /></div>
      <div><Label htmlFor="ai-rule-target">{t("target")}</Label><Input id="ai-rule-target" value={target} onChange={e => setTarget(e.target.value)} placeholder="addons/counterstrikesharp/plugins/..." /></div>
      <div>
        <Label htmlFor="ai-requirements">{t("requirements")}</Label>
        <Textarea id="ai-requirements" value={requirements} onChange={e => setRequirements(e.target.value)} aria-describedby="ai-requirements-hint" />
        <p id="ai-requirements-hint" className="text-xs text-fg-subtle">{t("requirementsHint")}</p>
      </div>
      <Button type="button" disabled={busy} onClick={async () => {
        setBusy(true); setError("");
        try {
          const response = await reviewAIPlugin(pluginId, { ...info, reviewed: true, installation: { asset_glob: asset, source_prefix: source, target_path: target || null }, requirements: requirements.split("\n").map(v => v.trim()).filter(Boolean) });
          if (response.ok) { setInfo(response.data.metadata); router.refresh(); } else setError(t("requestFailed"));
        } finally { setBusy(false); }
      }}>{t("saveReview")}</Button>
    </> : <><pre className="overflow-auto text-xs">{JSON.stringify(info.installation, null, 2)}</pre><ul>{info.requirements?.map((value,index) => <li key={index}>{value}</li>)}</ul></>}
    <div className="space-y-1 text-xs" data-testid="ai-review-notes">
      <p className="font-medium">{t("notes")}</p>
      {info.notes?.length ? <ul className="list-disc pl-5">{info.notes.map((value, index) => <li key={index} className="break-all">{value}</li>)}</ul> : <p className="text-fg-subtle">{t("notesEmpty")}</p>}
    </div>
    <div className="space-y-1 text-xs"><p>{t("sources")}</p>{info.sources?.map(item => <p key={item.path} className="break-all">{item.path} · {item.commit}</p>)}</div>
    {error && <p role="alert" className="text-danger">{error}</p>}
  </section>;
}
