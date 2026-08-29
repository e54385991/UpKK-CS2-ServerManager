"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import {
  refreshAiSettingsAction,
  saveAiSettingsAction,
  testAiSettingsAction,
} from "@/modules/settings/actions";
import type { AiProtocol, AiSystemPatch, AiSystemSettings } from "@/modules/settings/types";
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
import { Textarea } from "@/shared/ui/textarea";

function optionalNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

export function AiSettingsForm({ initial }: { initial: AiSystemSettings }) {
  const t = useTranslations("aiSettings");
  const [settings, setSettings] = useState(initial);
  const [enabled, setEnabled] = useState(initial.enabled);
  const [baseUrl, setBaseUrl] = useState(initial.baseUrl ?? "");
  const [model, setModel] = useState(initial.model ?? "");
  const [protocol, setProtocol] = useState<AiProtocol>(initial.apiProtocol);
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [adminPrompt, setAdminPrompt] = useState(initial.adminPrompt ?? "");
  const [allowlist, setAllowlist] = useState(initial.privateEndpointAllowlist.join("\n"));
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
  const [timeout, setTimeoutSeconds] = useState(String(initial.requestTimeoutSeconds));
  const [retention, setRetention] = useState(String(initial.historyRetentionDays));
  const [rounds, setRounds] = useState(String(initial.maxProviderRounds));
  const [toolCalls, setToolCalls] = useState(String(initial.maxToolCallsPerRound));
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  function patch(enabledValue: boolean): AiSystemPatch {
    return {
      enabled: enabledValue,
      baseUrl: baseUrl.trim() || null,
      model: model.trim() || null,
      apiProtocol: protocol,
      apiKey: apiKey.trim() || undefined,
      clearApiKey: clearKey,
      adminPrompt: adminPrompt.trim() || null,
      privateEndpointAllowlist: allowlist
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean),
      reasoningEffort: reasoning || null,
      temperature: optionalNumber(temperature),
      topP: optionalNumber(topP),
      maxCompletionTokens: Number(maxTokens) || settings.maxCompletionTokens,
      tokenLimitParameter: tokenField,
      frequencyPenalty: optionalNumber(frequency),
      presencePenalty: optionalNumber(presence),
      verbosity: verbosity || null,
      parallelToolCalls: parallelTools === "" ? null : parallelTools === "true",
      requestTimeoutSeconds: Number(timeout) || settings.requestTimeoutSeconds,
      historyRetentionDays: Number(retention) || settings.historyRetentionDays,
      maxProviderRounds: Number(rounds) || settings.maxProviderRounds,
      maxToolCallsPerRound: Number(toolCalls) || settings.maxToolCallsPerRound,
    };
  }

  function applySaved(next: AiSystemSettings) {
    setSettings(next);
    setEnabled(next.enabled);
    setApiKey("");
    setClearKey(false);
  }

  async function save() {
    setPending("save");
    setBanner(null);
    const result = await saveAiSettingsAction(patch(enabled));
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    applySaved(result.data);
    setBanner(t("saved"));
  }

  async function test() {
    setPending("test");
    setBanner(null);
    const result = await testAiSettingsAction();
    const refreshed = result.ok ? await refreshAiSettingsAction() : null;
    setPending(null);
    if (refreshed?.ok) applySaved(refreshed.data);
    setBanner(result.ok ? result.data.message : result.error || t("failed"));
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("help")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {banner ? <p className="text-sm text-fg-muted">{banner}</p> : null}
        <div className="flex flex-wrap gap-2">
          <Badge tone={settings.apiKeyConfigured ? "ok" : "neutral"}>
            {settings.apiKeyConfigured ? t("apiKeyOn") : t("apiKeyOff")}
          </Badge>
          <Badge tone={settings.providerTested ? "ok" : "neutral"}>{t("provider")}</Badge>
          <Badge tone={settings.toolCallingTested ? "ok" : "neutral"}>{t("tools")}</Badge>
          <Badge tone={settings.streamingTested ? "ok" : "neutral"}>{t("streaming")}</Badge>
        </div>
        <div className="flex items-center justify-between gap-3">
          <Label htmlFor="ai-enabled">{t("enabled")}</Label>
          <Switch
            id="ai-enabled"
            label={t("enabled")}
            checked={enabled}
            onCheckedChange={setEnabled}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="ai-url">{t("baseUrl")}</Label>
          <Input id="ai-url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="ai-model">{t("model")}</Label>
          <Input id="ai-model" value={model} onChange={(event) => setModel(event.target.value)} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="ai-protocol">{t("protocol")}</Label>
          <Select
            id="ai-protocol"
            value={protocol}
            onChange={(event) => setProtocol(event.target.value as AiProtocol)}
          >
            <option value="chat_completions">chat_completions</option>
            <option value="responses">responses</option>
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="ai-key">{t("apiKey")}</Label>
          <Input
            id="ai-key"
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={t("apiKeyKeep")}
          />
        </div>
        <div className="flex items-center justify-between gap-3">
          <Label htmlFor="ai-clear">{t("clearKey")}</Label>
          <Switch
            id="ai-clear"
            label={t("clearKey")}
            checked={clearKey}
            onCheckedChange={setClearKey}
          />
        </div>
        <details className="rounded-md border border-line px-3 py-2">
          <summary className="cursor-pointer text-sm font-medium text-fg">
            {t("advanced")}
          </summary>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="ai-reasoning">{t("reasoning")}</Label>
              <Select
                id="ai-reasoning"
                value={reasoning}
                onChange={(event) => setReasoning(event.target.value)}
              >
                <option value="">{t("providerDefault")}</option>
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
              <Label htmlFor="ai-verbosity">{t("verbosity")}</Label>
              <Select
                id="ai-verbosity"
                value={verbosity}
                onChange={(event) => setVerbosity(event.target.value)}
              >
                <option value="">{t("providerDefault")}</option>
                <option value="low">low</option>
                <option value="medium">medium</option>
                <option value="high">high</option>
              </Select>
            </div>
            <div>
              <Label htmlFor="ai-max-tokens">{t("maxTokens")}</Label>
              <Input
                id="ai-max-tokens"
                type="number"
                min={256}
                max={32768}
                value={maxTokens}
                onChange={(event) => setMaxTokens(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="ai-token-field">{t("tokenField")}</Label>
              <Select
                id="ai-token-field"
                value={tokenField}
                onChange={(event) => setTokenField(event.target.value)}
              >
                <option value="max_completion_tokens">max_completion_tokens</option>
                <option value="max_tokens">max_tokens</option>
                <option value="omit">{t("omit")}</option>
              </Select>
            </div>
            <div>
              <Label htmlFor="ai-temperature">{t("temperature")}</Label>
              <Input
                id="ai-temperature"
                type="number"
                min={0}
                max={2}
                step={0.1}
                value={temperature}
                onChange={(event) => setTemperature(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="ai-top-p">top_p</Label>
              <Input
                id="ai-top-p"
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={topP}
                onChange={(event) => setTopP(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="ai-frequency">{t("frequency")}</Label>
              <Input
                id="ai-frequency"
                type="number"
                min={-2}
                max={2}
                step={0.1}
                value={frequency}
                onChange={(event) => setFrequency(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="ai-presence">{t("presence")}</Label>
              <Input
                id="ai-presence"
                type="number"
                min={-2}
                max={2}
                step={0.1}
                value={presence}
                onChange={(event) => setPresence(event.target.value)}
              />
            </div>
            <div className="sm:col-span-2">
              <Label htmlFor="ai-parallel">{t("parallel")}</Label>
              <Select
                id="ai-parallel"
                value={parallelTools}
                onChange={(event) => setParallelTools(event.target.value)}
              >
                <option value="">{t("providerDefault")}</option>
                <option value="true">{t("enabledOption")}</option>
                <option value="false">{t("disabledOption")}</option>
              </Select>
            </div>
          </div>
          <p className="mt-2 text-xs text-fg-subtle">{t("advancedHint")}</p>
        </details>
        <div className="space-y-2">
          <Label htmlFor="ai-prompt">{t("adminPrompt")}</Label>
          <Textarea
            id="ai-prompt"
            value={adminPrompt}
            onChange={(event) => setAdminPrompt(event.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="ai-allowlist">{t("allowlist")}</Label>
          <Textarea
            id="ai-allowlist"
            value={allowlist}
            onChange={(event) => setAllowlist(event.target.value)}
            placeholder="http://10.0.0.8:8000"
          />
          <p className="text-xs text-fg-subtle">{t("allowlistHint")}</p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="ai-timeout">{t("timeout")}</Label>
            <Input
              id="ai-timeout"
              value={timeout}
              onChange={(event) => setTimeoutSeconds(event.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="ai-retention">{t("retention")}</Label>
            <Input
              id="ai-retention"
              value={retention}
              onChange={(event) => setRetention(event.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="ai-rounds">{t("rounds")}</Label>
            <Input id="ai-rounds" value={rounds} onChange={(event) => setRounds(event.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="ai-tools">{t("toolCalls")}</Label>
            <Input
              id="ai-tools"
              value={toolCalls}
              onChange={(event) => setToolCalls(event.target.value)}
            />
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" disabled={Boolean(pending)} onClick={() => void save()}>
            {pending === "save" ? t("saving") : t("save")}
          </Button>
          <Button type="button" variant="outline" disabled={Boolean(pending)} onClick={() => void test()}>
            {pending === "test" ? t("testing") : t("test")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
