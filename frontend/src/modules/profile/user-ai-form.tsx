"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { saveProfileAiAction, testProfileAiAction } from "@/modules/profile/actions";
import type { AiMode, AiProtocol, ProfileAiSettings } from "@/modules/profile/types";
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
import { Switch } from "@/shared/ui/switch";

function optionalNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

export function UserAiForm({ initial }: { initial: ProfileAiSettings }) {
  const t = useTranslations("profile");
  const [settings, setSettings] = useState(initial);
  const [mode, setMode] = useState<AiMode>(initial.mode);
  const [baseUrl, setBaseUrl] = useState(initial.baseUrl ?? "");
  const [model, setModel] = useState(initial.model ?? "");
  const [protocol, setProtocol] = useState<AiProtocol>(initial.apiProtocol);
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [reasoning, setReasoning] = useState(initial.reasoningEffort ?? "");
  const [verbosity, setVerbosity] = useState(initial.verbosity ?? "");
  const [maxTokens, setMaxTokens] = useState(String(initial.maxCompletionTokens));
  const [tokenField, setTokenField] = useState(initial.tokenLimitParameter);
  const [temperature, setTemperature] = useState(
    initial.temperature == null ? "" : String(initial.temperature),
  );
  const [topP, setTopP] = useState(initial.topP == null ? "" : String(initial.topP));
  const [frequency, setFrequency] = useState(
    initial.frequencyPenalty == null ? "" : String(initial.frequencyPenalty),
  );
  const [presence, setPresence] = useState(
    initial.presencePenalty == null ? "" : String(initial.presencePenalty),
  );
  const [parallelTools, setParallelTools] = useState(
    initial.parallelToolCalls == null ? "" : String(initial.parallelToolCalls),
  );
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  async function save() {
    setPending("save");
    setBanner(null);
    const result = await saveProfileAiAction({
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
      parallelToolCalls:
        parallelTools === "" ? null : parallelTools === "true",
    });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setSettings(result.data);
    setApiKey("");
    setClearKey(false);
    setBanner(t("aiSaved"));
  }

  async function test() {
    setPending("test");
    setBanner(null);
    const result = await testProfileAiAction({
      baseUrl: baseUrl.trim() || undefined,
      model: model.trim() || undefined,
      apiKey: apiKey.trim() || undefined,
    });
    setPending(null);
    setBanner(result.ok ? result.data.message : result.error || t("failed"));
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
          <p className="text-sm text-fg-muted" role="status">
            {banner}
          </p>
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
