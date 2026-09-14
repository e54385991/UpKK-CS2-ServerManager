import type { AbstractIntlMessages, AppConfig } from "next-intl";

type Messages = AppConfig["Messages"];

export type MessageNamespace =
  | (keyof Messages & string)
  | `${keyof Messages & string}.${string}`;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return value != null && typeof value === "object" && !Array.isArray(value);
}

function assignPath(
  target: Record<string, unknown>,
  source: Record<string, unknown>,
  parts: readonly string[],
  namespace: string,
): void {
  const head = parts[0];
  if (head == null || !(head in source)) {
    throw new Error(`Missing message namespace: ${namespace}`);
  }
  const value = source[head];
  if (parts.length === 1) {
    target[head] = value;
    return;
  }
  if (!isPlainObject(value)) {
    throw new Error(`Message namespace is not nested: ${namespace}`);
  }
  const existing = target[head];
  const next = isPlainObject(existing) ? existing : {};
  if (existing !== next) target[head] = next;
  assignPath(next, value, parts.slice(1), namespace);
}

/**
 * Copy named top-level or dotted namespaces from the server catalog.
 * Nested providers replace the client dictionary, so callers must pass
 * every namespace that subtree needs. Missing paths throw instead of
 * silently dropping copy.
 */
export function pickMessages(
  catalog: AbstractIntlMessages,
  namespaces: readonly MessageNamespace[],
): AbstractIntlMessages {
  const result: Record<string, unknown> = {};
  const root = catalog as Record<string, unknown>;
  for (const namespace of namespaces) {
    assignPath(result, root, namespace.split("."), namespace);
  }
  return result as AbstractIntlMessages;
}
