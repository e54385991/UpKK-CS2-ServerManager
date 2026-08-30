const LANGUAGE_ALIASES: Record<string, string> = {
  bash: "bash",
  conf: "cfg",
  env: "properties",
  htm: "html",
  ini: "properties",
  jsonc: "json",
  log: "cfg",
  properties: "properties",
  sh: "sh",
  vdf: "cfg",
  yml: "yaml",
};

export function editorLanguageId(name: string): string {
  const base = (name.split("/").pop() || name).toLowerCase();
  if (base === ".env" || base === ".gitignore") return "properties";
  const dot = base.lastIndexOf(".");
  if (dot <= 0) return "";
  const ext = base.slice(dot + 1);
  return LANGUAGE_ALIASES[ext] ?? ext;
}
