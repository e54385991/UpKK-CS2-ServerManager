export const PROXY_MODES = ["direct", "panel", "github_url"] as const;
export const EMAIL_PROVIDERS = ["smtp", "gmail"] as const;

export type ProxyMode = (typeof PROXY_MODES)[number];
export type EmailProvider = (typeof EMAIL_PROVIDERS)[number];

export type SystemSettings = {
  readonly defaultProxyMode: ProxyMode;
  readonly githubProxyUrl: string | null;
  readonly hasGlobalGithubToken: boolean;
  readonly globalGithubTokenPrefix: string | null;
  readonly emailEnabled: boolean;
  readonly emailProvider: EmailProvider;
  readonly emailFromAddress: string | null;
  readonly emailFromName: string | null;
  readonly smtpHost: string | null;
  readonly smtpPort: number | null;
  readonly smtpUsername: string | null;
  readonly smtpUseTls: boolean;
  readonly hasSmtpPassword: boolean;
  readonly hasGmailCredentials: boolean;
  readonly hasGmailToken: boolean;
  readonly gmailReady: boolean;
  readonly updatedAt: string | null;
};

export type SettingsPatch = {
  readonly defaultProxyMode?: ProxyMode;
  readonly githubProxyUrl?: string | null;
  readonly globalGithubToken?: string;
  readonly clearGlobalGithubToken?: boolean;
  readonly emailEnabled?: boolean;
  readonly emailProvider?: EmailProvider;
  readonly emailFromAddress?: string | null;
  readonly emailFromName?: string | null;
  readonly smtpHost?: string | null;
  readonly smtpPort?: number | null;
  readonly smtpUsername?: string | null;
  readonly smtpPassword?: string;
  readonly smtpUseTls?: boolean;
};

export function isProxyMode(value: string): value is ProxyMode {
  return (PROXY_MODES as readonly string[]).includes(value);
}

export function isEmailProvider(value: string): value is EmailProvider {
  return (EMAIL_PROVIDERS as readonly string[]).includes(value);
}

export type AiProtocol = "chat_completions" | "responses";
export const AI_CONTEXT_WINDOW_OPTIONS = [
  8192,
  16384,
  32768,
  65536,
  131072,
  262144,
  393216,
  1048576,
] as const;
export type AiContextWindowTokens = (typeof AI_CONTEXT_WINDOW_OPTIONS)[number];

export function toAiContextWindowTokens(value: number): AiContextWindowTokens {
  if (value === 8192) return 8192;
  if (value === 16384) return 16384;
  if (value === 32768) return 32768;
  if (value === 65536) return 65536;
  if (value === 131072) return 131072;
  if (value === 393216) return 393216;
  if (value === 1048576) return 1048576;
  return 262144;
}

export type AiSystemSettings = {
  readonly enabled: boolean;
  readonly baseUrl: string | null;
  readonly model: string | null;
  readonly apiProtocol: AiProtocol;
  readonly apiKeyConfigured: boolean;
  readonly adminPrompt: string | null;
  readonly privateEndpointAllowlist: readonly string[];
  readonly reasoningEffort: string | null;
  readonly temperature: number | null;
  readonly topP: number | null;
  readonly maxCompletionTokens: number;
  readonly tokenLimitParameter: string;
  readonly frequencyPenalty: number | null;
  readonly presencePenalty: number | null;
  readonly verbosity: string | null;
  readonly parallelToolCalls: boolean | null;
  readonly contextWindowTokens: AiContextWindowTokens;
  readonly requestTimeoutSeconds: number;
  readonly historyRetentionDays: number;
  readonly maxProviderRounds: number;
  readonly maxToolCallsPerRound: number;
  readonly providerTested: boolean;
  readonly toolCallingTested: boolean;
  readonly streamingTested: boolean;
};

export type AiSystemPatch = {
  readonly enabled?: boolean;
  readonly baseUrl?: string | null;
  readonly model?: string | null;
  readonly apiProtocol?: AiProtocol;
  readonly apiKey?: string;
  readonly clearApiKey?: boolean;
  readonly adminPrompt?: string | null;
  readonly privateEndpointAllowlist?: readonly string[];
  readonly reasoningEffort?: string | null;
  readonly temperature?: number | null;
  readonly topP?: number | null;
  readonly maxCompletionTokens?: number;
  readonly tokenLimitParameter?: string;
  readonly frequencyPenalty?: number | null;
  readonly presencePenalty?: number | null;
  readonly verbosity?: string | null;
  readonly parallelToolCalls?: boolean | null;
  readonly contextWindowTokens?: AiContextWindowTokens;
  readonly requestTimeoutSeconds?: number;
  readonly historyRetentionDays?: number;
  readonly maxProviderRounds?: number;
  readonly maxToolCallsPerRound?: number;
};
