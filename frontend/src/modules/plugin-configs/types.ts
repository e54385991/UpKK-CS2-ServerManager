export type PluginConfigSourceKind = "file" | "directory";

export type PluginConfigSource = {
  readonly id: number | null;
  readonly path: string;
  readonly absolutePath: string;
  readonly name: string;
  readonly type: PluginConfigSourceKind;
  readonly isDefault: boolean;
  readonly persisted: boolean;
};

export type PluginConfigWorkspace = {
  readonly serverId: number;
  readonly gameDirectory: string;
  readonly sources: readonly PluginConfigSource[];
};

export type PluginConfigBrowseItem = {
  readonly name: string;
  readonly path: string | null;
  readonly type: "file" | "directory" | "symlink";
  readonly selectable: boolean;
  readonly size: number;
};

export type PluginConfigBrowse = {
  readonly path: string;
  readonly items: readonly PluginConfigBrowseItem[];
};

export type PluginConfigScannedFile = {
  readonly name: string;
  readonly path: string;
  readonly treePath: string;
  readonly size: number;
  readonly modified: number;
  readonly format: string;
  readonly tooLarge: boolean;
};

export type PluginConfigFieldValue = boolean | number | string | null;

export type PluginConfigField = {
  readonly id: string;
  readonly key: string;
  readonly group: string;
  readonly kind: string;
  readonly value: PluginConfigFieldValue;
  readonly line: number;
  readonly comment: string;
};

export type PluginConfigFile = {
  readonly path: string;
  readonly name: string;
  readonly format: string;
  readonly revision: string;
  readonly content: string;
  readonly visualSupported: boolean;
  readonly parseError: string | null;
  readonly fields: readonly PluginConfigField[];
  readonly message: string | null;
};

export type PluginConfigMutation = {
  readonly success: boolean;
};

export type PluginConfigEditMode = "visual" | "raw";

export type PluginConfigFileGroup = {
  readonly path: string;
  readonly name: string;
  readonly depth: number;
  readonly files: readonly PluginConfigScannedFile[];
};

export type PluginConfigFieldGroup = {
  readonly name: string;
  readonly fields: readonly PluginConfigField[];
};

export function groupConfigFiles(
  files: readonly PluginConfigScannedFile[],
  search: string,
  rootLabel: string,
): PluginConfigFileGroup[] {
  const needle = search.trim().toLowerCase();
  const groups = new Map<string, PluginConfigScannedFile[]>();
  for (const file of files) {
    if (needle && !file.treePath.toLowerCase().includes(needle)) continue;
    const slash = file.treePath.lastIndexOf("/");
    const folder = slash >= 0 ? file.treePath.slice(0, slash) : "";
    if (!folder) {
      if (!groups.has("")) groups.set("", []);
    } else {
      const parts = folder.split("/");
      for (let index = 1; index <= parts.length; index += 1) {
        const ancestor = parts.slice(0, index).join("/");
        if (!groups.has(ancestor)) groups.set(ancestor, []);
      }
    }
    groups.get(folder)?.push(file);
  }
  return Array.from(groups.entries())
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([path, grouped]) => ({
      path,
      name: path ? (path.split("/").pop() ?? path) : rootLabel,
      depth: path ? path.split("/").length - 1 : 0,
      files: grouped,
    }));
}

export function groupConfigFields(
  fields: readonly PluginConfigField[],
  search: string,
): PluginConfigFieldGroup[] {
  const needle = search.trim().toLowerCase();
  const groups = new Map<string, PluginConfigField[]>();
  for (const field of fields) {
    const haystack = `${field.group} ${field.key} ${field.comment}`.toLowerCase();
    if (needle && !haystack.includes(needle)) continue;
    const list = groups.get(field.group) ?? [];
    list.push(field);
    groups.set(field.group, list);
  }
  return Array.from(groups.entries()).map(([name, grouped]) => ({
    name,
    fields: grouped,
  }));
}

export function formatConfigSize(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KiB", "MiB"] as const;
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

export function formatConfigTimestamp(timestamp: number): string {
  return timestamp ? new Date(timestamp * 1000).toLocaleString() : "—";
}
