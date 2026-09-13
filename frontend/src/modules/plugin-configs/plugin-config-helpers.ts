import type {
  PluginConfigField,
  PluginConfigFieldValue,
  PluginConfigFile,
  PluginConfigScannedFile,
} from "@/modules/plugin-configs/types";

export type Banner = { readonly tone: "ok" | "warn" | "danger"; readonly text: string };

export type SourceRuntime = {
  readonly loaded: boolean;
  readonly loading: boolean;
  readonly files: readonly PluginConfigScannedFile[];
  readonly fileCount: number;
  readonly truncated: boolean;
  readonly scanPath: string;
};

export type ScanEvent =
  | { type: "start" }
  | { type: "progress"; directory?: string; count?: number }
  | { type: "file"; file?: Record<string, unknown> }
  | { type: "complete"; count?: number; truncated?: boolean }
  | { type: "error"; detail?: string };

export const EMPTY_RUNTIME: SourceRuntime = {
  loaded: false,
  loading: false,
  files: [],
  fileCount: 0,
  truncated: false,
  scanPath: "",
};

export function toScannedFile(raw: Record<string, unknown>): PluginConfigScannedFile {
  return {
    name: String(raw.name || ""),
    path: String(raw.path || ""),
    treePath: String(raw.tree_path || raw.treePath || ""),
    size: Number(raw.size || 0),
    modified: Number(raw.modified || 0),
    format: String(raw.format || "raw"),
    tooLarge: Boolean(raw.too_large ?? raw.tooLarge),
  };
}

export function valuesFromFile(file: PluginConfigFile): Record<string, PluginConfigFieldValue> {
  const values: Record<string, PluginConfigFieldValue> = {};
  for (const field of file.fields) values[field.id] = field.value;
  return values;
}

export function parseFieldInput(
  field: PluginConfigField,
  value: string | boolean,
): PluginConfigFieldValue {
  if (field.kind === "boolean") return Boolean(value);
  if (field.kind === "integer") {
    if (value === "") return null;
    const parsed = Number.parseInt(String(value), 10);
    return Number.isNaN(parsed) ? null : parsed;
  }
  if (field.kind === "number") {
    if (value === "") return null;
    const parsed = Number(value);
    return Number.isNaN(parsed) ? null : parsed;
  }
  return String(value);
}
