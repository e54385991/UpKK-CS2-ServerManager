"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import {
  loadSystemAiSettings,
  saveSystemAiSettings,
  testSystemAiProvider,
} from "@/modules/settings/ai-settings-client";
import { EMPTY_AI_SYSTEM_SETTINGS } from "@/modules/settings/ai-wire";
import {
  AI_CONTEXT_WINDOW_OPTIONS,
  type AiProtocol,
  type AiSystemPatch,
  type AiSystemSettings,
  toAiContextWindowTokens,
} from "@/modules/settings/types";
import { alertDialog, notify } from "@/shared/feedback";
import { providerTestAlert, providerTestErrorAlert } from "@/modules/settings/ai-test-result";
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
import { cn } from "@/shared/lib/cn";
import { ShieldCheck, TriangleAlert } from "lucide-react";

type Banner = { readonly tone: "ok" | "warn" | "danger"; readonly text: string };

function optionalNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

export function AiSettingsForm({
  initial,
}: {
  initial: AiSystemSettings | null;
}) {
  const t = useTranslations("aiSettings");
  const seed = initial ?? EMPTY_AI_SYSTEM_SETTINGS;
  const [settings, setSettings] = useState(seed);
  const [enabled, setEnabled] = useState(seed.enabled);
  const [baseUrl, setBaseUrl] = useState(seed.baseUrl ?? "");
  const [model, setModel] = useState(seed.model ?? "");
  const [protocol, setProtocol] = useState<AiProtocol>(seed.apiProtocol);
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [adminPrompt, setAdminPrompt] = useState(seed.adminPrompt ?? "");
  const [allowlist, setAllowlist] = useState(seed.privateEndpointAllowlist.join("\n"));
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
  const [contextWindow, setContextWindow] = useState(String(seed.contextWindowTokens));
  const [timeout, setTimeoutSeconds] = useState(String(seed.requestTimeoutSeconds));
  const [retention, setRetention] = useState(String(seed.historyRetentionDays));
  const [rounds, setRounds] = useState(String(seed.maxProviderRounds));
  const [toolCalls, setToolCalls] = useState(String(seed.maxToolCallsPerRound));
  const [pending, setPending] = useState<string | null>(initial ? null : "load");
  const [banner, setBanner] = useState<Banner | null>(
    initial ? null : { tone: "warn", text: t("loading") },
  );

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
      contextWindowTokens: toAiContextWindowTokens(Number(contextWindow)),
      requestTimeoutSeconds: Number(timeout) || settings.requestTimeoutSeconds,
      historyRetentionDays: Number(retention) || settings.historyRetentionDays,
      maxProviderRounds: Number(rounds) || settings.maxProviderRounds,
      maxToolCallsPerRound: Number(toolCalls) || settings.maxToolCallsPerRound,
    };
  }

  function applySaved(next: AiSystemSettings) {
    setSettings(next);
    setEnabled(next.enabled);
    setBaseUrl(next.baseUrl ?? "");
    setModel(next.model ?? "");
    setProtocol(next.apiProtocol);
    setApiKey("");
    setClearKey(false);
    setAdminPrompt(next.adminPrompt ?? "");
    setAllowlist(next.privateEndpointAllowlist.join("\n"));
    setReasoning(next.reasoningEffort ?? "");
    setVerbosity(next.verbosity ?? "");
    setMaxTokens(String(next.maxCompletionTokens));
    setTokenField(next.tokenLimitParameter);
    setTemperature(next.temperature == null ? "" : String(next.temperature));
    setTopP(next.topP == null ? "" : String(next.topP));
    setFrequency(next.frequencyPenalty == null ? "" : String(next.frequencyPenalty));
    setPresence(next.presencePenalty == null ? "" : String(next.presencePenalty));
    setParallelTools(next.parallelToolCalls == null ? "" : String(next.parallelToolCalls));
    setContextWindow(String(next.contextWindowTokens));
    setTimeoutSeconds(String(next.requestTimeoutSeconds));
    setRetention(String(next.historyRetentionDays));
    setRounds(String(next.maxProviderRounds));
    setToolCalls(String(next.maxToolCallsPerRound));
  }

  useEffect(() => {
    if (initial) return;
    let cancelled = false;
    void loadSystemAiSettings().then((result) => {
      if (cancelled) return;
      setPending(null);
      if (!result.ok) {
        setBanner({ tone: "danger", text: result.error });
        return;
      }
      applySaved(result.data);
      setBanner(null);
    });
    return () => {
      cancelled = true;
    };
  }, [initial]); // t is a stable translator; do not retrigger on locale object identity

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
      const result = await saveSystemAiSettings(patch(enabled));
      if (!result.ok) {
        showBanner({ tone: "danger", text: result.error || t("failed") });
        return;
      }
      applySaved(result.data);
      showBanner({ tone: "ok", text: t("saved") });
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
    setBanner({ tone: "warn", text: t("testingHint") });
    try {
      const result = await testSystemAiProvider();
      const refreshed = await loadSystemAiSettings();
      if (refreshed.ok) applySaved(refreshed.data);
      if (!result.ok) {
        const text = result.error || t("failed");
        showBanner({ tone: "danger", text });
        await alertDialog(providerTestErrorAlert(text, t));
        return;
      }
      const dialog = providerTestAlert(result.data, t);
      showBanner({
        tone: dialog.tone === "ok" ? "ok" : "danger",
        text: dialog.title || result.data.message || t("failed"),
      });
      await alertDialog(dialog);
    } catch (error) {
      const text = error instanceof Error ? error.message : t("failed");
      showBanner({ tone: "danger", text });
      await alertDialog(providerTestErrorAlert(text, t));
    } finally {
      setPending(null);
    }
  }

  return (
    <Card className="max-w-2xl" data-testid="ai-settings-card">
      <CardHeader>
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("help")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {banner ? (
          <div
            role="status"
            data-testid="ai-settings-banner"
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
              <Label htmlFor="ai-context-window">{t("contextWindow")}</Label>
              <Select
                id="ai-context-window"
                value={contextWindow}
                onChange={(event) => setContextWindow(event.target.value)}
              >
                {AI_CONTEXT_WINDOW_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    {value === 262144 ? "256K" : value === 393216 ? "384K" : "1M"}
                  </option>
                ))}
              </Select>
              <p className="mt-1 text-xs text-fg-subtle">{t("contextWindowHint")}</p>
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
        <p className="text-xs text-fg-subtle">{t("testingHint")}</p>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            data-testid="ai-settings-save"
            disabled={Boolean(pending)}
            onClick={() => void save()}
          >
            {pending === "save" ? t("saving") : t("save")}
          </Button>
          <Button
            type="button"
            variant="outline"
            data-testid="ai-settings-test"
            disabled={Boolean(pending)}
            onClick={() => void test()}
          >
            {pending === "test" ? t("testing") : t("test")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
