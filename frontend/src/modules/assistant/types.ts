export type AssistantConversation = {
  readonly id: string;
  readonly serverId: number | null;
  readonly title: string;
};

export type AssistantMessage = {
  readonly id: number;
  readonly role: string;
  readonly content: string | null;
  readonly toolName: string | null;
};

export type AssistantWorkspace = {
  readonly providerReady: boolean;
  readonly mode: "global" | "custom" | "none";
  readonly model: string | null;
  readonly conversations: readonly AssistantConversation[];
};

export type AssistantConversationDetail = AssistantConversation & {
  readonly messages: readonly AssistantMessage[];
};

export type AssistantRun = {
  readonly id: string;
  readonly conversationId: string;
  readonly status: string;
  readonly error: string | null;
};

export type AssistantTool = {
  readonly id: string;
  readonly toolName: string;
  readonly argumentsHash: string;
  readonly risk: string;
  readonly status: string;
  readonly requiresApproval: boolean;
  readonly error: string | null;
  readonly arguments: Record<string, unknown>;
  readonly summary: unknown;
};

export type AssistantRunDetail = AssistantRun & {
  readonly tools: readonly AssistantTool[];
};

export type AssistantServerOption = {
  readonly id: number;
  readonly name: string;
  readonly host: string;
  readonly gamePort: number;
  readonly sshUser: string;
  readonly status: string;
};

export const ASSISTANT_EXAMPLE_KEYS = [
  "cssLogs",
  "crashIsolation",
  "marketInstall",
  "githubInstall",
  "githubSearch",
  "workshop",
] as const;

export type AssistantExampleKey = (typeof ASSISTANT_EXAMPLE_KEYS)[number];
