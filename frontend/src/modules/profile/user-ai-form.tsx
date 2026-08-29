"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { EMPTY_PROFILE_AI_SETTINGS } from "@/modules/profile/ai-wire";
import type { AiMode, AiProtocol, ProfileAiPatch, ProfileAiSettings } from "@/modules/profile/types";
import {
  loadProfileAiSettings,
  saveProfileAiSettings,
  testProfileAiProvider,
} from "@/modules/settings/ai-settings-client";
import { alertDialog, notify } from "@/shared/feedback";
import { providerTestAlert, providerTestErrorAlert } from "@/modules/settings/ai-test-result";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { ShieldCheck, TriangleAlert } from "lucide-react";
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

function optionalNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

type Banner = { readonly tone: "ok" | "warn" | "danger"; readonly text: string };

export function UserAiForm({ initial }: { initial: ProfileAiSettings | null }) {
  const t = useTranslations("profile");
  const tAi = useTranslations("aiSettings");
  const seed = initial ?? EMPTY_PROFILE_AI_SETTINGS;
  const [settings, setSettings] = useState(seed);
  const [mode, setMode] = useState<AiMode>(seed.mode);
  const [baseUrl, setBaseUrl] = useState(seed.baseUrl ?? "");
  const [model, setModel] = useState(seed.model ?? "");
  const [protocol, setProtocol] = useState<AiProtocol>(seed.apiProtocol);
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [reasoning, setReasoning] = useState(seed.reasoningEffort ?? "");
  const [verbosity, setVerbosity] = useState(seed.verbosity ?? "");
  const [maxTokens, setMaxTokens] = useState(String(seed.maxCompletionTokens));
  const [tokenField, setTokenField] = useState(seed.tokenLimitParameter);
  const [temperature, setTemperature] = useState(
    seed.temperature == null ? "" : String(seed.temperature),
  );
  const [topP, setTopP] = useState(seed.topP == null ? "" : String(seed.topP));
  const [frequency, setFrequency] = useState(
    seed.frequencyPenalty == null ? "" : String(seed.frequencyPenalty),
  );
  const [presence, setPresence] = useState(
    seed.presencePenalty == null ? "" : String(seed.presencePenalty),
  );
  const [parallelTools, setParallelTools] = useState(
    seed.parallelToolCalls == null ? "" : String(seed.parallelToolCalls),
  );
  const [pending, setPending] = useState<string | null>(initial ? null : "load");
  const [banner, setBanner] = useState<Banner | null>(null);

  function patch(): ProfileAiPatch {
    return {
      mode,
      baseUrl: baseUrl.trim() || null,
      model: model.trim() || null,
      apiProtocol: protocol,
      apiKey: apiKey.trim() || undefined,
      clearApiKey: clearKey,
      reasoningEffort: reasoning || null,
      temperature: optionalNumber(temperature),
      topP: optionalNumber(topP),
      maxCompletionTokens: Number(maxTokens) || settings.maxCompletionTokens,
      tokenLimitParameter: tokenField,
      frequencyPenalty: optionalNumber(frequency),
      presencePenalty: optionalNumber(presence),
      verbosity: verbosity || null,
      parallelToolCalls: parallelTools === "" ? null : parallelTools === "true",
    };
  }

  function applySaved(next: ProfileAiSettings) {
    setSettings(next);
    setMode(next.mode);
    setBaseUrl(next.baseUrl ?? "");
    setModel(next.model ?? "");
    setProtocol(next.apiProtocol);
    setApiKey("");
    setClearKey(false);
    setReasoning(next.reasoningEffort ?? "");
    setVerbosity(next.verbosity ?? "");
    setMaxTokens(String(next.maxCompletionTokens));
    setTokenField(next.tokenLimitParameter);
    setTemperature(next.temperature == null ? "" : String(next.temperature));
    setTopP(next.topP == null ? "" : String(next.topP));
    setFrequency(next.frequencyPenalty == null ? "" : String(next.frequencyPenalty));
    setPresence(next.presencePenalty == null ? "" : String(next.presencePenalty));
    setParallelTools(next.parallelToolCalls == null ? "" : String(next.parallelToolCalls));
  }

  useEffect(() => {
    if (initial) return;
    let cancelled = false;
    void loadProfileAiSettings().then((result) => {
      if (cancelled) return;
      setPending(null);
      if (!result.ok) {
        setBanner({ tone: "danger", text: result.error });
        return;
      }
      applySaved(result.data);
    });
    return () => {
      cancelled = true;
    };
  }, [initial]);

  function showBanner(next: Banner) {
    setBanner(next);
    if (next.tone === "ok") notify.success(next.text);
    else if (next.tone === "danger") notify.error(next.text);
    else notify.warning(next.text);
  }

  async function save() {
    setPending("save");
    setBanner(null);
    try {
      const result = await saveProfileAiSettings(patch());
      if (!result.ok) {
        showBanner({ tone: "danger", text: result.error || t("failed") });
        return;
      }
      applySaved(result.data);
      showBanner({ tone: "ok", text: t("aiSaved") });
    } catch (error) {
      showBanner({
        tone: "danger",
        text: error instanceof Error ? error.message : t("failed"),
      });
    } finally {
      setPending(null);
    }
  }

  async function test() {
    setPending("test");
    setBanner({ tone: "warn", text: t("aiTestingHint") });
    try {
      const result = await testProfileAiProvider();
      const refreshed = await loadProfileAiSettings();
      if (refreshed.ok) applySaved(refreshed.data);
      if (!result.ok) {
        const text = result.error || t("failed");
        showBanner({ tone: "danger", text });
        await alertDialog(providerTestErrorAlert(text, tAi));
        return;
      }
      const dialog = providerTestAlert(result.data, tAi);
      showBanner({
        tone: dialog.tone === "ok" ? "ok" : "danger",
        text: dialog.title || result.data.message || t("failed"),
      });
      await alertDialog(dialog);
    } catch (error) {
      const text = error instanceof Error ? error.message : t("failed");
      showBanner({ tone: "danger", text });
      await alertDialog(providerTestErrorAlert(text, tAi));
    } finally {
      setPending(null);
    }
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <div className="space-y-1">
          <CardTitle>{t("aiTitle")}</CardTitle>
          <CardDescription>{t("aiHelp")}</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap gap-2">
          <Badge tone={settings.apiKeyConfigured ? "ok" : "neutral"}>
            {settings.apiKeyConfigured ? t("aiKeyOn") : t("aiKeyOff")}
          </Badge>
          <Badge tone={settings.providerTested ? "ok" : "neutral"}>{t("aiProvider")}</Badge>
          <Badge tone={settings.toolCallingTested ? "ok" : "neutral"}>{t("aiTools")}</Badge>
          <Badge tone={settings.streamingTested ? "ok" : "neutral"}>{t("aiStreaming")}</Badge>
        </div>
        <div>
          <Label htmlFor="profile-ai-mode">{t("aiMode")}</Label>
          <Select
            id="profile-ai-mode"
            value={mode}
            onChange={(event) => setMode(event.target.value as AiMode)}
          >
            <option value="global">{t("aiUseGlobal")}</option>
            <option value="custom">{t("aiUseCustom")}</option>
          </Select>
        </div>
        {mode === "custom" ? (
          <div className="space-y-4 rounded-md border border-line px-4 py-3">
            <div>
              <Label htmlFor="profile-ai-url">{t("aiBaseUrl")}</Label>
              <Input
                id="profile-ai-url"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="profile-ai-model">{t("aiModel")}</Label>
              <Input
                id="profile-ai-model"
                value={model}
                onChange={(event) => setModel(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="profile-ai-protocol">{t("aiProtocol")}</Label>
              <Select
                id="profile-ai-protocol"
                value={protocol}
                onChange={(event) => setProtocol(event.target.value as AiProtocol)}
              >
                <option value="chat_completions">{t("aiProtocolChat")}</option>
                <option value="responses">{t("aiProtocolResponses")}</option>
              </Select>
            </div>
            <div>
              <Label htmlFor="profile-ai-key">{t("aiApiKey")}</Label>
              <Input
                id="profile-ai-key"
                type="password"
                autoComplete="new-password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder={t("aiApiKeyKeep")}
              />
            </div>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="profile-ai-clear" className="mb-0">
                {t("aiClearKey")}
              </Label>
              <Switch
                id="profile-ai-clear"
                label={t("aiClearKey")}
                checked={clearKey}
                onCheckedChange={setClearKey}
              />
            </div>
            <details className="rounded-md border border-line px-3 py-2">
              <summary className="cursor-pointer text-sm font-medium text-fg">
                {t("aiAdvanced")}
              </summary>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <div>
                  <Label htmlFor="profile-ai-reasoning">{t("aiReasoning")}</Label>
                  <Select
                    id="profile-ai-reasoning"
                    value={reasoning}
                    onChange={(event) => setReasoning(event.target.value)}
                  >
                    <option value="">{t("aiProviderDefault")}</option>
                    <option value="none">none</option>
                    <option value="minimal">minimal</option>
                    <option value="low">low</option>
                    <option value="medium">medium</option>
                    <option value="high">high</option>
                    <option value="xhigh">xhigh</option>
                    <option value="max">max</option>
                    <option value="ultra">ultra</option>
                  </Select>
                </div>
                <div>
                  <Label htmlFor="profile-ai-verbosity">{t("aiVerbosity")}</Label>
                  <Select
                    id="profile-ai-verbosity"
                    value={verbosity}
                    onChange={(event) => setVerbosity(event.target.value)}
                  >
                    <option value="">{t("aiProviderDefault")}</option>
                    <option value="low">low</option>
                    <option value="medium">medium</option>
                    <option value="high">high</option>
                  </Select>
                </div>
                <div>
                  <Label htmlFor="profile-ai-max-tokens">{t("aiMaxTokens")}</Label>
                  <Input
                    id="profile-ai-max-tokens"
                    type="number"
                    min={256}
                    max={32768}
                    value={maxTokens}
                    onChange={(event) => setMaxTokens(event.target.value)}
                  />
                </div>
                <div>
                  <Label htmlFor="profile-ai-token-field">{t("aiTokenField")}</Label>
                  <Select
                    id="profile-ai-token-field"
                    value={tokenField}
                    onChange={(event) => setTokenField(event.target.value)}
                  >
                    <option value="max_completion_tokens">max_completion_tokens</option>
                    <option value="max_tokens">max_tokens</option>
                    <option value="omit">{t("aiOmit")}</option>
                  </Select>
                </div>
                <div>
                  <Label htmlFor="profile-ai-temperature">{t("aiTemperature")}</Label>
                  <Input
                    id="profile-ai-temperature"
                    type="number"
                    min={0}
                    max={2}
                    step={0.1}
                    value={temperature}
                    onChange={(event) => setTemperature(event.target.value)}
                  />
                </div>
                <div>
                  <Label htmlFor="profile-ai-top-p">top_p</Label>
                  <Input
                    id="profile-ai-top-p"
                    type="number"
                    min={0}
                    max={1}
                    step={0.05}
                    value={topP}
                    onChange={(event) => setTopP(event.target.value)}
                  />
                </div>
                <div>
                  <Label htmlFor="profile-ai-frequency">{t("aiFrequency")}</Label>
                  <Input
                    id="profile-ai-frequency"
                    type="number"
                    min={-2}
                    max={2}
                    step={0.1}
                    value={frequency}
                    onChange={(event) => setFrequency(event.target.value)}
                  />
                </div>
                <div>
                  <Label htmlFor="profile-ai-presence">{t("aiPresence")}</Label>
                  <Input
                    id="profile-ai-presence"
                    type="number"
                    min={-2}
                    max={2}
                    step={0.1}
                    value={presence}
                    onChange={(event) => setPresence(event.target.value)}
                  />
                </div>
                <div className="sm:col-span-2">
                  <Label htmlFor="profile-ai-parallel">{t("aiParallel")}</Label>
                  <Select
                    id="profile-ai-parallel"
                    value={parallelTools}
                    onChange={(event) => setParallelTools(event.target.value)}
                  >
                    <option value="">{t("aiProviderDefault")}</option>
                    <option value="true">{t("aiEnabledOption")}</option>
                    <option value="false">{t("aiDisabledOption")}</option>
                  </Select>
                </div>
              </div>
              <p className="mt-2 text-xs text-fg-subtle">{t("aiAdvancedHint")}</p>
            </details>
          </div>
        ) : null}
        {banner ? (
          <div
            role="status"
            className={cn(
              "flex items-start gap-2 rounded-md border px-4 py-3 text-sm",
              banner.tone === "ok" && "border-ok/30 bg-ok-muted/40 text-ok",
              banner.tone === "warn" && "border-warn/30 bg-warn-muted/40 text-warn",
              banner.tone === "danger" && "border-danger/30 bg-danger-muted/40 text-danger",
            )}
          >
            {banner.tone === "ok" ? (
              <ShieldCheck className="mt-0.5 size-4 shrink-0" />
            ) : (
              <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            )}
            <span>{banner.text}</span>
          </div>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button type="button" disabled={Boolean(pending)} onClick={() => void save()}>
            {pending === "save" ? t("saving") : t("aiSave")}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={Boolean(pending) || mode !== "custom"}
            onClick={() => void test()}
          >
            {pending === "test" ? t("testing") : t("aiTest")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
