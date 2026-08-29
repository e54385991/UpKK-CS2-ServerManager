export type AlertTone = "danger" | "default";

export type AlertOptions = {
  title?: string;
  description?: string;
  confirmLabel?: string;
  tone?: AlertTone;
};

export type AlertRequest = {
  readonly id: number;
  readonly options: AlertOptions;
};

type Listener = () => void;

let nextId = 1;
let current: AlertRequest | null = null;
const queued: Array<{
  options: AlertOptions;
  resolve: () => void;
}> = [];
const resolvers = new Map<number, () => void>();
const listeners = new Set<Listener>();

function emit() {
  for (const listener of listeners) listener();
}

function promote() {
  if (current) return;
  const next = queued.shift();
  if (!next) {
    emit();
    return;
  }
  const request: AlertRequest = { id: nextId++, options: next.options };
  current = request;
  resolvers.set(request.id, next.resolve);
  emit();
}

export function subscribeAlert(listener: Listener) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getAlertSnapshot() {
  return current;
}

export function getAlertServerSnapshot() {
  return null;
}

export function resolveAlert() {
  if (!current) return;
  const resolve = resolvers.get(current.id);
  resolvers.delete(current.id);
  current = null;
  resolve?.();
  promote();
}

export function alertDialog(input: string | AlertOptions): Promise<void> {
  const options: AlertOptions =
    typeof input === "string"
      ? { description: input, tone: "danger" }
      : { tone: "danger", ...input };

  return new Promise((resolve) => {
    queued.push({ options, resolve });
    promote();
  });
}
