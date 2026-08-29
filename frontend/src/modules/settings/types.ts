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
  readonly requestTimeoutSeconds?: number;
  readonly historyRetentionDays?: number;
  readonly maxProviderRounds?: number;
  readonly maxToolCallsPerRound?: number;
};
