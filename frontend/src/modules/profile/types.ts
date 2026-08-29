export type ProfileSettings = {
  readonly id: number;
  readonly username: string;
  readonly email: string | null;
  readonly isAdmin: boolean;
  readonly isActive: boolean;
  readonly createdAt: string | null;
  readonly steamcmdMaxRetries: number;
  readonly steamcmdMaxRetriesDefault: number;
  readonly steamcmdMaxRetriesLimit: number;
  readonly hasSteamApiKey: boolean;
  readonly steamApiKeyPrefix: string | null;
  readonly hasGithubToken: boolean;
  readonly githubTokenPrefix: string | null;
  readonly hasApiKey: boolean;
};

export type ProfileCredentialsPatch = {
  readonly email?: string | null;
  readonly steamApiKey?: string;
  readonly clearSteamApiKey?: boolean;
  readonly githubToken?: string;
  readonly clearGithubToken?: boolean;
  readonly captchaToken: string;
  readonly captchaCode: string;
};

export type ProfileS3Settings = {
  readonly enabled: boolean;
  readonly endpointUrl: string | null;
  readonly region: string | null;
  readonly bucket: string | null;
  readonly accessKeyId: string | null;
  readonly prefix: string | null;
  readonly useSsl: boolean;
  readonly retentionCount: number;
  readonly hasSecret: boolean;
  readonly isConfigured: boolean;
};

export type ProfileS3Patch = {
  readonly enabled: boolean;
  readonly endpointUrl: string | null;
  readonly region: string | null;
  readonly bucket: string | null;
  readonly accessKeyId: string | null;
  readonly secretAccessKey?: string;
  readonly prefix: string | null;
  readonly useSsl: boolean;
  readonly retentionCount: number;
  readonly clearSecret?: boolean;
  readonly captchaToken: string;
  readonly captchaCode: string;
};

export type ProfileS3Test = {
  readonly success: boolean;
  readonly message: string;
  readonly steps: readonly { name: string; status: string; message: string }[];
};

export type ProfileApiKey = {
  readonly apiKey: string;
  readonly createdAt: string | null;
};

export type AiProtocol = "chat_completions" | "responses";
export type AiMode = "global" | "custom";

export type ProfileAiSettings = {
  readonly mode: AiMode;
  readonly baseUrl: string | null;
  readonly model: string | null;
  readonly apiProtocol: AiProtocol;
  readonly apiKeyConfigured: boolean;
  readonly reasoningEffort: string | null;
  readonly temperature: number | null;
  readonly topP: number | null;
  readonly maxCompletionTokens: number;
  readonly tokenLimitParameter: string;
  readonly frequencyPenalty: number | null;
  readonly presencePenalty: number | null;
  readonly verbosity: string | null;
  readonly parallelToolCalls: boolean | null;
  readonly providerTested: boolean;
  readonly toolCallingTested: boolean;
  readonly streamingTested: boolean;
  readonly effectiveEnabled: boolean;
  readonly effectiveSource: "global" | "custom" | "none";
};

export type ProfileAiPatch = {
  readonly mode: AiMode;
  readonly baseUrl?: string | null;
  readonly model?: string | null;
  readonly apiProtocol?: AiProtocol;
  readonly apiKey?: string;
  readonly clearApiKey?: boolean;
  readonly reasoningEffort?: string | null;
  readonly temperature?: number | null;
  readonly topP?: number | null;
  readonly maxCompletionTokens?: number;
  readonly tokenLimitParameter?: string;
  readonly frequencyPenalty?: number | null;
  readonly presencePenalty?: number | null;
  readonly verbosity?: string | null;
  readonly parallelToolCalls?: boolean | null;
};
