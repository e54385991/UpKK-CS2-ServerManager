export type GameArgumentSetting = {
  name: string;
  description: string;
  kind: "token" | "integer" | "choice";
  maxLength: number;
  minimum: number;
  maximum: number;
  choices: string[];
};

export type PersonalGameCommand = {
  name: string;
  description: string;
  arguments: GameArgumentSetting[];
  enabled: boolean;
  requiresEmpty: boolean;
  requiresOnline: boolean;
};
